#!/usr/bin/env python
import re
import os
import sys
import logging
import socket
import urllib2
import xml.etree.ElementTree as ET
import time
import ConfigParser
from optparse import OptionParser

from plex_tvst_scrobbler.tvst import Tvst

def get_plex_access_token(config):
    if os.path.exists(config.get('plex-tvst-scrobbler', 'plex_access_token_location')):
        plexfp = open(config.get('plex-tvst-scrobbler', 'plex_access_token_location'), 'r')
        plex_access_token = plexfp.read().strip()
        plexfp.close()
    return plex_access_token

def fetch_metadata(urlpath, config, plex_access_token):
    ''' retrieves the metadata information from the Plex media Server api. '''

    logger = logging.getLogger(__name__)
    url = '{url}{urlpath}'.format(url=config.get('plex-tvst-scrobbler',
      'mediaserver_url'), urlpath=urlpath)
    logger.info('Fetching library metadata from {url}'.format(url=url))

    headers = None

    if plex_access_token:
        headers = {'X-Plex-Token': plex_access_token}

    # fail if request is greater than 2 seconds.
    try:
        request = urllib2.Request(url, None, headers)
        metadata = urllib2.urlopen(request, timeout=2)
    except urllib2.URLError, e:
        logger.error('urllib2 error reading from {url} \'{error}\''.format(url=url,
                      error=e))
        return False
    except socket.timeout, e:
        logger.error('Timeout reading from {url} \'{error}\''.format(url=url, error=e))
        return False

    tree = ET.fromstring(metadata.read())
    video = tree.find('Video')

    if video is None:
        logger.info('Ignoring played item library-id={urlpath}, could not determine video library information.'.
                format(urlpath=urlpath))
        return False

    if video.get('type') != 'episode':
        logger.info('Ignoring played item library-id={urlpath}, because it is not an episode.'.
                format(urlpath=urlpath))
        return False

    # matching from the guid field, which should provide the agent TVDB result
    episode = video.get('guid')
    show_name = video.get('grandparentTitle')

    regex = re.compile('com.plexapp.agents.thetvdb://([0-9]+)/([0-9]+)/([0-9]+)\?.*')
    m = regex.match(episode)

    if m:
        episode_label = "{0} S{1}E{2}".format(show_name,
                                              m.group(2).zfill(2),
                                              m.group(3).zfill(2))
        logger.info("Matched TV show {0}".format(episode_label))
    else:
        return False

    return {
        'show_id': m.group(1),
        'season_number': m.group(2),
        'number': m.group(3)
    }


def process_watched_episodes(config, syncall):

    logger = logging.getLogger(__name__)
    plex_access_token = get_plex_access_token(config)

    url = '{url}/status/sessions/history/all'.format(url=config.get('plex-tvst-scrobbler',
      'mediaserver_url'))
    logger.info('Fetching watched history {url}'.format(url=url))

    headers = None

    if plex_access_token:
        headers = {'X-Plex-Token': plex_access_token}

    try:
        request = urllib2.Request(url, None, headers)
        response = urllib2.urlopen(request)
    except urllib2.URLError, e:
        logger.error('urllib2 error reading from {url} \'{error}\''.format(url=url,
                      error=e))
        return False
    except socket.timeout, e:
        logger.error('Timeout reading from {url} \'{error}\''.format(url=url, error=e))
        return False

    tree = ET.fromstring(response.read())
    yesterday = time.time() - 24*60*60

    for video in tree.iter('Video'):
        if video.get('type') == 'episode':
            if int(video.get('viewedAt')) > yesterday or syncall:
                metadata = fetch_metadata(video.get('key'), config, plex_access_token)

                if not metadata: continue

                # submit to tvshowtime.com
                a = tvst.scrobble(metadata['show_id'], metadata['season_number'],
                                  metadata['number'])

                # scrobble was not successful , FIXME: do something?
                # if not a:


if __name__ == '__main__':

    p = OptionParser()
    p.add_option('-c', '--config', action='store', dest='config_file',
        help='The location to the configuration file.')
    p.add_option('-p', '--precheck', action='store_true', dest='precheck',
        default=False, help='Run a pre-check to ensure a correctly configured system.')
    p.add_option('-a', '--authenticate', action='store_true', dest='authenticate',
        default=False, help='Generate a new Plex and TV Time session key.')
    p.add_option('--all', action='store_true', dest='syncall',
        default=False, help='Sync all episodes from Plex API to TV Time. Default sync pass 24 hours.')

    programDir = os.path.abspath(os.path.dirname(os.path.realpath(sys.argv[0])))
    p.set_defaults(config_file=os.path.expanduser(
      os.path.join(programDir, 'conf/plex_tvst_scrobbler.conf')))

    (options, args) = p.parse_args()

    if not os.path.exists(options.config_file):
        print 'Exiting, unable to locate config file {0}. use -c to specify config target'.format(
            options.config_file)
        sys.exit(1)

    # apply defaults to *required* configuration values.
    config = ConfigParser.ConfigParser(defaults = {
        'config file location': options.config_file,
        'session': os.path.expanduser('~/.config/plex-tvst-scrobbler/session_key'),
        'plex_access_token_location': os.path.expanduser('~/.config/plex-tvst-scrobbler/plex_access_token'),
        'mediaserver_url': 'http://localhost:32400',
        'log_file': '/tmp/plex_tvst_scrobbler.log'
      })
    config.read(options.config_file)

    FORMAT = '%(asctime)-15s [%(process)d] [%(name)s %(funcName)s] [%(levelname)s] %(message)s'
    logging.basicConfig(filename=config.get('plex-tvst-scrobbler',
      'log_file'), format=FORMAT, level=logging.DEBUG)
    logger = logging.getLogger('main')

    # dump our configuration values to the logfile
    for key in config.items('plex-tvst-scrobbler'):
        logger.debug('config : {0} -> {1}'.format(key[0], key[1]))

    tvst = Tvst(config)

    # if a plex token object does not exist, prompt user 
    # to authenticate to plex.tv to get a plex access token
    if (not os.path.exists(config.get('plex-tvst-scrobbler','plex_access_token_location')) or
      options.authenticate):
        logger.info('Prompting to authenticate to plex.tv.')
        result = False
        while not result:
            result = tvst.plex_auth()

    # if a valid session object does not exist, prompt user
    # to authenticate.
    if (not os.path.exists(config.get('plex-tvst-scrobbler','session')) or
      options.authenticate):
        logger.info('Prompting to authenticate to TV Time.')
        tvst.tvst_auth()
        print 'Please relaunch plex-tvst-scrobbler service.'
        logger.warn('Exiting application.')
        sys.exit(0)

    logger.debug('using tvshowtime.com session key={key} , st_mtime={mtime}'.format(
        key=config.get('plex-tvst-scrobbler','session'),
        mtime=time.ctime(os.path.getmtime(config.get('plex-tvst-scrobbler','session'))) ))

    process_watched_episodes(config, options.syncall)
#!/usr/bin/env python
#
# Copyright 2013 Seth Golub
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import print_function

import logging
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from xml.etree.cElementTree import ElementTree
from urllib import unquote
from urlparse import urlparse
from mapperfs import MapFuse, TrivialMapper, FlatMapper, CommonMapper
from fuse import FUSE

def _playlist_files(pl):
    '''Return a list of files in the playlist, given the etree element
    corresponding to the playlist.
    '''
    return [unquote(urlparse(el.text).path) for el in pl.findall('location')]

def all_playlists(filename):
    '''
    Return a dict of all playlists in the Rhythmbox XML file, whose
    keys are the playlist names and whose values are lists of files.
    '''
    tree = ElementTree(file=filename)
    return { pl.get('name') : _playlist_files(pl)
             for pl in tree.findall("playlist") }

def one_playlist(filename, playlistname):
    '''Return the files in the playlist in the Rhythmbox XML file.'''
    tree = ElementTree(file=filename)
    for pl in tree.findall("playlist"):
        # My old version of etree doesn't support attributes in the
        # path spec, so we check them manually.
        if pl.get('name') == playlistname:
            return _playlist_files(pl)
    raise ValueError('Playlist not found: ' + playlistname)

class PlaylistReader:
    def __init__(self, xmlfile, playlistname):
        self.xmlfile = xmlfile
        self.playlistname = playlistname

    def files(self):
        return one_playlist(self.xmlfile, self.playlistname)

def main():
    description = 'mount a single rhythmbox playlist as a filesystem'

    mappers = {'copy': TrivialMapper,
               'flat': FlatMapper,
               'common': CommonMapper }

    parser = ArgumentParser(description=description,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument('-m', '--mapper', choices=mappers.keys(),
                        default='copy',
                        help='method of mapping filenames into the filesystem')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('file', help='Rhythmbox playlist file')
    parser.add_argument('playlist', help='playlist name')
    parser.add_argument('mountpoint', help='target directory')
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    mapper = mappers[args.mapper]()
    src = lambda: mapper.pairs(one_playlist(args.file, args.playlist))
    fuse = FUSE(MapFuse(src, [args.file]), args.mountpoint, foreground=True)


if __name__ == '__main__':
    main()

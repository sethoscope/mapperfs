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


from __future__ import with_statement

from errno import EACCES, ENOENT
from threading import Thread, Lock
from collections import defaultdict
from itertools import izip
import stat
import logging
import fileinput
import os
import time

# https://github.com/terencehonles/fusepy
from fuse import FUSE, FuseOSError, Operations, LoggingMixIn

try:
    import inotifyx
except ImportError:
    logging.warning('inotifyx module not found; file watching not supported')

class Directory(set):
    def num_subdirs(self):
        return sum(1 for e in self if isinstance(e, Directory))

class WatcherThread(Thread):
    def __init__(self, mapfuse, watch_files):
        super(WatcherThread, self).__init__()
        self.watch_files = watch_files
        self.mapfuse = mapfuse
        
    def run(self):
        logging.debug('starting watcher thread')
        mask = inotifyx.IN_MODIFY | inotifyx.IN_CLOSE_WRITE
        in_fd = inotifyx.init()
        for f in self.watch_files:
            inotifyx.add_watch(in_fd, f, mask)
            logging.debug('watching ' + f)
        while True:
            logging.debug('watcher waiting for events')
            inotifyx.get_events(in_fd)
            logging.debug('watcher got change event')
            self.mapfuse.read_list()


class MapFuse(LoggingMixIn, Operations):
    def __init__(self, pair_source, watch_files):
        self.pair_source = pair_source
        self.rwlock = Lock()
        self.update_lock = Lock()
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.watch_files = watch_files
        self.read_list()

    def read_list(self):
        entries = { mounted.rstrip('/'): real.rstrip('/')
                    for (real, mounted) in self.pair_source() }
        dirs = self._synthesize_dirs(entries)
        logging.debug('init with: ' + str(entries))
        with self.update_lock:
            self.entries = entries
            self.dirs = dirs
        self.ctime = time.time()

    @staticmethod
    def _synthesize_dirs(entries):
        '''Return the directories needed to reach the entries in the form
        { path : set(contents), ... }'''
        dirs = defaultdict(Directory)
        for e in entries:
            d, base = os.path.split(e)
            while d and base and not (d in entries or (d in dirs and base in dirs[d])):
                dirs[d].add(base)
                d, base = os.path.split(d)
        logging.debug('dir tree: ' + str(dirs))
        return dirs

    def _find_referent(self, path):
        logging.debug('lookup: ' + path)
        with self.update_lock:
            if path in self.entries:
                logging.debug('  resolved %s to %s' % (path, self.entries[path]))
                return self.entries[path]
            if path in self.dirs:
                logging.debug('  resolved %s to a directory' % path)
                return self.dirs[path]
            # Perhaps it's under a directory we've exposed
            logging.debug('  could it be under a mounted directory?')
            left, base = os.path.split(path)
            right = base
            while base and not (left in self.entries or left in self.dirs):
                left, base = os.path.split(left)
                right = os.path.join(base, right)
            if left:
                logging.debug('  found %s' % left)
                if left in self.entries and os.path.isdir(self.entries[left]):
                    logging.debug('  which might contain ' + right)
                    return os.path.join(self.entries[left], right)
            raise FuseOSError(ENOENT)

    def __call__(self, op, path, *args):
        real_path = self._find_referent(path)
        logging.debug('calling %s with %s (%s) %s' % (op, path, real_path, str(args)))
        return Operations.__call__(self, op, real_path, *args)

    def noaccess(self, *args):
        raise FuseOSError(EACCES)

    def access(self, path, mode):
        '''Returns 0 if access is permitted, -1 otherwise.'''
        if isinstance(path, Directory):
            return -1 if (mode & os.W_OK) else 0
        return 0 if os.access(path, mode) else -1

    def init(self, path):
        if self.watch_files:
            watch_thread = WatcherThread(self, self.watch_files)
            watch_thread.start()

    chmod = os.chmod
    chown = os.chown

    create = noaccess

    def flush(self, path, fh):
        return os.fsync(fh)

    def fsync(self, path, datasync, fh):
        return os.fsync(fh)

    def getattr(self, path, fh=None):
        if not isinstance(path, Directory):
            st = os.lstat(path)
            return dict((key, getattr(st, key)) for key in ('st_atime', 'st_ctime',
                'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))
        logging.debug('getattr of a directory')
        return { 'st_atime' : self.ctime,
                 'st_ctime' : self.ctime,
                 'st_gid' : self.gid,
                 'st_mode' : stat.S_IFDIR | 0o555,
                 'st_mtime' : self.ctime,
                 'st_nlink' : 2 + path.num_subdirs(),
                 'st_size' : len(path),
                 'st_uid' : self.uid }

    getxattr = None
    listxattr = None

    link = noaccess
    mkdir = noaccess
    mknod = noaccess

    open = os.open

    def read(self, path, size, offset, fh):
        with self.rwlock:
            os.lseek(fh, offset, 0)
            return os.read(fh, size)

    def readdir(self, path, fh):
        if isinstance(path, Directory):
            return ['.', '..'] + list(path)
        return ['.', '..'] + os.listdir(path)

    readlink = os.readlink

    def release(self, path, fh):
        return os.close(fh)

    rename = noaccess
    rmdir = noaccess

    def statfs(self, path):
        if not isinstance(path, Directory):
            stv = os.statvfs(path)
            return dict((key, getattr(stv, key)) for key in ('f_bavail', 'f_bfree',
                'f_blocks', 'f_bsize', 'f_favail', 'f_ffree', 'f_files', 'f_flag',
                'f_frsize', 'f_namemax'))
        return { 'f_bavail' : 0,
                 'f_bfree' : 0,
                 'f_blocks' : 0,
                 'f_bsize' : 1,
                 'f_favail' : 0,
                 'f_ffree' : 0,
                 'f_files' : 0,
                 'f_flag' : 0,
                 'f_frsize' : 0,
                 'f_namemax' : 0 }

    symlink = noaccess

    def truncate(self, path, length, fh=None):
        with open(path, 'r+') as f:
            f.truncate(length)

    unlink = noaccess
    utimens = os.utime

    def write(self, path, data, offset, fh):
        with self.rwlock:
            os.lseek(fh, offset, 0)
            return os.write(fh, data)


def listify(iterable):
    '''Return a list version of iterable.'''
    if isinstance(iterable, list):
        return iterable
    return list(iterable)

class TrivialMapper:
    def pairs(self, files):
        for f in files:
            yield f, f

class FlatMapper:
    fmt = '{base}-{n}{ext}'

    def pairs(self, files):
        files = listify(files)
        flat = self._flat_with_collisions(files)
        flat = self._uncollide(flat)
        return izip(files, flat)
    
    @staticmethod
    def _flat_with_collisions(files):
        return ['/' + os.path.basename(f.rstrip('/')) for f in files]

    def _uncollide(self, files):
        '''Yields files from input list, such that duplicate filenames
        are renamed according to self.fmt.'''
        # We'll go through the files in the order received, renaming any
        # that conflict with ones we've already yielded, but choosing
        # the new names such that they don't preempt any actual files
        # later in the list.  Sadly, this requires slupring up the whole
        # list at the start, which is unlikely to be an actual problem
        # for anyone, but it's nice when we can stick to O(1) generator
        # chains.
        reserved = set(files)
        yielded = set()
        for f in files:
            if f in yielded:
                f = self._new_name(f, reserved)
            yield f
            yielded.add(f)
            reserved.add(f)

    def _new_name(self, filename, reserved):
        base, ext = os.path.splitext(filename)
        n = 1
        while True:
            new_name = self.fmt.format(base=base, ext=ext, n=n)
            if new_name not in reserved:
                return new_name
            n += 1


class CommonMapper:
    def pairs(self, files):
        files = listify(files)
        prefix = self._longest_common_path(files)
        logging.debug('longest common prefix: ' + prefix)
        prefix_len = len(prefix)
        trimmed = (f[prefix_len:] for f in files)
        return izip(files, trimmed)

    @staticmethod
    def _longest_common_path(file_list):
        '''Return the longest path that refers to a directory under
        which all the filenames can be found.  This is similar to
        os.path.commonprefix(), but the result is guaranteed to be a
        directory, at least as implied by the filenames.
        '''
        prefix = os.path.commonprefix(file_list)
        i = prefix.rfind('/')
        if i == -1:  # not found!
            return ''
        return prefix[:i]


def read_files(input_files):
    '''Yields lines from input files, ignoring lines starting
    with ; or # and removing surrounding quote marks.
    '''
    for line in fileinput.input(input_files):
        line = line.strip(' \"\t\n')
        if not line.startswith('#') and not line.startswith(';'):
            yield line

def main():
    # I don't expect this command line application to be very useful.
    # It's more of a proof of concept and rough test.  The real value
    # will come from using this module in other code, where the files
    # and their visible mount points are based on something interesting.

    # Note that you can specify both stdin and other input files, but
    # if you do, you probably want to use --once.  Otherwise when a
    # file changes, we'll throw away whatever we read from stdin the
    # first time.  We could avoid this by keeping track of which files
    # we got from each input source and only rereading the input file
    # that changed.  But we don't do that, because it's complicated
    # and probably not useful to anyone.

    mappers = {'copy': TrivialMapper,
               'flat': FlatMapper,
               'common': CommonMapper }

    from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
    description = 'Expose existing files in a new filesystem at mountpoint.'
    parser = ArgumentParser(description=description,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument('-m', '--mapper', choices=mappers.keys(),
                        default='copy',
                        help='method of mapping filenames into the filesystem')
    parser.add_argument('-o', '--once', action='store_true',
                         help='''only read input files once,
                         rather than rereading them when they change''')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('mountpoint',
                        help='directory at which to mount the new filesystem')
    parser.add_argument('inputfile', nargs='+',
                        help="""File listing files, one per line, that
                        should exist in the new filesystem. - specifies
                        stdin.  (You can list stdin more than once, but
                        you probably don't want to.  You can list both
                        stdin and regular files, but if you do, you
                        should probably also use --once.)""")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    logging.debug('Mounting to ' + args.mountpoint)

    mapper = mappers[args.mapper]()
    pair_source = lambda: mapper.pairs(read_files(args.inputfile))

    watch = [] if args.once else [i for i in args.inputfile if i != '-']
    fuse = FUSE(MapFuse(pair_source, watch), args.mountpoint, foreground=True)
    
if __name__ == '__main__':
    main()


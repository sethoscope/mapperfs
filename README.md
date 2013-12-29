# mapperfs

mapperfs is a read-only user-mounted filesystem (via [FUSE](http://fuse.sourceforge.net/)) that simply exposes other existing files that you tell it to.  You could achieve the same thing with hard links (if all your source files and your mount point are on one filesystem) or with symlinks (if you don't mind having symlinks instead of regular files), but mapperfs will update the filesystem when your input file changes, so you can mount it once and not have to maintain a bunch of links.

It can be useful as-is if you have simple input files (I use it with GQView/Geeqie collections), or you can easily extend it to work with more complex input files.  For example, rhythmboxfs exposes a single [Rhythmbox](http://www.gnome.org/projects/rhythmbox/) playlist as a filesystem.  (The order of the playlist is not conveyed, just the contents.)

## path shortening

There are three options for how file paths appear in your new
filesystem.  Below are explanations and examples of each method.


###copy - copy the whole path

    % mapperfs.py --mapper copy /mnt /tmp/list-of-yam-files
    % find /mnt -type f
    /mnt/home/seth/lib/recipes/mashed-yams
    /mnt/home/seth/lib/stories/mashed-yams
    /mnt/home/seth/lib/stories/baked-yams

###flat - put all files in one directory

    % mapperfs.py --mapper flat /mnt /tmp/list-of-yam-files
    % find /mnt -type f
    /mnt/mashed-yams
    /mnt/mashed-yams-1
    /mnt/baked-yams

###common -  trim off whatever directories all files have in common

    % mapperfs.py --mapper common /mnt /tmp/list-of-yam-files
    % find /mnt -type f
    /mnt/recipes/mashed-yams
    /mnt/stories/mashed-yams
    /mnt/stories/baked-yams

## dependencies

For FUSE support, this uses
[fusepy](https://github.com/terencehonles/fusepy), which I've included
here, since it wasn't available in Debian.

Monitoring the input file depends on [inotifyx](http://www.alittletooquiet.net/software/inotifyx/).  If you don't have that installed, everything should work fine, but your filesystem won't be updated automatically.


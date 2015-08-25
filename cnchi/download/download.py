#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# download.py
#
#  Copyright © 2013-2015 Antergos
#
#  This file is part of Cnchi.
#
#  Cnchi is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
#
#  Cnchi is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  The following additional terms are in effect as per Section 7 of the license:
#
#  The preservation of all legal notices and author attributions in
#  the material or in the Appropriate Legal Notices displayed
#  by works containing it is required.
#
#  You should have received a copy of the GNU General Public License
#  along with Cnchi; If not, see <http://www.gnu.org/licenses/>.


""" Module to download packages """

import os
import logging
import queue

if __name__ == '__main__':
    import sys
    # Insert the parent directory at the front of the path.
    # This is used only when we want to test
    base_dir = os.path.dirname(__file__) or '.'
    parent_dir = os.path.join(base_dir, '..')
    sys.path.insert(0, parent_dir)

import pacman.pac as pac

import download.metalink as ml
import download.download_urllib as download_urllib
import download.download_aria2 as download_aria2
import download.download_requests as download_requests

from misc.misc import InstallError


class DownloadPackages(object):
    """ Class to download packages using Aria2, requests (default) or urllib
        This class tries to previously download all necessary packages for
        Antergos installation using aria2, requests or urllib
        Aria2 is known to use too much memory (not Aria2's fault but ours)
        so until it's fixed it it's not advised to use it """

    def __init__(
            self,
            package_names,
            download_module='requests',
            pacman_conf_file=None,
            pacman_cache_dir=None,
            cache_dir=None,
            callback_queue=None,
            settings=None):
        """ Initialize DownloadPackages class. Gets default configuration """

        if pacman_conf_file is None:
            self.pacman_conf_file = "/etc/pacman.conf"
        else:
            self.pacman_conf_file = pacman_conf_file

        if pacman_cache_dir is None:
            self.pacman_cache_dir = "/install/var/cache/pacman/pkg"
        else:
            self.pacman_cache_dir = pacman_cache_dir

        if cache_dir is None or not os.path.exists(cache_dir):
            self.cache_dir = "/var/cache/pacman/pkg"
        else:
            self.cache_dir = cache_dir

        # Create pacman cache dir (it's ok if it already exists)
        os.makedirs(pacman_cache_dir, mode=0o755, exist_ok=True)

        # Stores last issued event for each event type
        # (to prevent repeating events)
        self.last_event = {}
        self.callback_queue = callback_queue
        self.settings = settings
        self.download_module = download_module

        self.downloads_list = None

    def start(self):
        # Create downloads list from package list
        if self.downloads_list is None:
            self.create_downloads_list(package_names)

        if self.downloads_list is None:
            # Still none? Error
            txt = _("Can't create download package list. Check log output for details")
            raise InstallError(txt)

        logging.debug("Using %s module to download packages", self.download_module)

        if self.download_module == "aria2":
            download = download_aria2.Download(
                pacman_cache_dir,
                cache_dir,
                callback_queue)
        elif self.download_module == "urllib":
            download = download_urllib.Download(
                pacman_cache_dir,
                cache_dir,
                callback_queue)
        else:
            if self.download_module != "requests":
                logging.debug("Unknown module '%s', Cnchi will use the 'requests' one as default", self.download_module)
            download = download_requests.Download(
                pacman_cache_dir,
                cache_dir,
                callback_queue)

        if not download.start(self.downloads_list):
            self.settings.set('failed_download', True)
            # New: When we can't download (even one package), we stop right here
            # Pros: The user will be prompted immediately when a package fails
            # to download
            # Cons: We won't let alpm to try to download the package itself
            txt = _("Can't download needed packages. Cnchi can't continue.")
            raise InstallError(txt)

    def create_downloads_list(self, package_names):
        """ Creates a downloads list from the package list """
        self.queue_event('percent', '0')
        self.queue_event('info', _('Creating the list of packages to download...'))
        processed_packages = 0
        total_packages = len(package_names)

        self.downloads_list = {}

        try:
            pacman = pac.Pac(
                conf_path=self.pacman_conf_file,
                callback_queue=self.callback_queue)
            if pacman is None:
                return None
        except Exception as err:
            logging.error("Can't initialize pyalpm: %s", err)
            self.downloads_list = None
            return

        try:
            for package_name in package_names:
                metalink = ml.create(pacman, package_name, self.pacman_conf_file)
                if metalink is None:
                    logging.error("Error creating metalink for package %s. Installation will stop", package_name)
                    txt = _("Error creating metalink for package {0}. Installation will stop").format(package_name)
                    raise InstallError(txt)

                # Get metalink info
                metalink_info = ml.get_info(metalink)

                # Update downloads list with the new info from the processed metalink
                for key in metalink_info:
                    if key not in self.downloads_list:
                        self.downloads_list[key] = metalink_info[key]

                # Show progress to the user
                processed_packages += 1
                percent = round(float(processed_packages / total_packages), 2)
                self.queue_event('percent', str(percent))
        except Exception as err:
            logging.error("Can't create download set: %s", err)
            self.downloads_list = None
            return

        try:
            pacman.release()
            del pacman
        except Exception as err:
            logging.error("Can't release pyalpm: %s", err)
            self.downloads_list = None
            return

        # Overwrite last event (to clean up the last message)
        self.queue_event('info', "")

    def queue_event(self, event_type, event_text=""):
        """ Adds an event to Cnchi event queue """

        if self.callback_queue is None:
            if event_type != "percent":
                logging.debug("{0}:{1}".format(event_type, event_text))
            return

        if event_type in self.last_event:
            if self.last_event[event_type] == event_text:
                # do not repeat same event
                return

        self.last_event[event_type] = event_text

        try:
            # Add the event
            self.callback_queue.put_nowait((event_type, event_text))
        except queue.Full:
            pass


''' Test case '''
if __name__ == '__main__':
    import gettext

    _ = gettext.gettext

    formatter = logging.Formatter(
        '[%(asctime)s] [%(module)s] %(levelname)s: %(message)s',
        "%Y-%m-%d %H:%M:%S")
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    DownloadPackages(
        package_names=["gnome-sudoku"],
        download_module="urllib",
        cache_dir="",
        pacman_cache_dir="/tmp/pkg")

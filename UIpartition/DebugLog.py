#
#    uipartition - A disk partitions/RAID/LVM setup tool
#    Copyright (C) 2010-2015  MontaVista Software, LLC
#
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, write to the Free Software
#    Foundation, Inc., 51 Franklin Street, Fifth Floor,
#      Boston, MA  02110-1301  USA

class DebugLog:
    def __init__(self, filename):
        self.filename = filename
        self.debugfile = None
        return
    
    # Send a string to the debug log
    def _log(self, str):
        if (not self.debugfile):
            self.debugfile = open(self.filename, "w")
            pass

        self.debugfile.write(str + "\n")
        return

debuglog = DebugLog("/tmp/part.debug")

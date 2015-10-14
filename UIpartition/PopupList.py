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

import curses
import FlexScrollColumn
import DebugLog

class PopupList:
    def __init__(self, parent, y, x, list, curr,
                 doneHandler = None, doneObj=None):
        self.done = False
        self.doneHandler = doneHandler
        self.doneObj = doneObj

        self.list = list
        
        maxwidth = 0
        nlines = 0
        for i in list:
            if (len(str(i)) > maxwidth):
                maxwidth = len(str(i))
                pass
            nlines += 1
            pass

        self.nlines = nlines
        
        self.borderwin = parent.derwin(nlines + 2, maxwidth + 2, y, x)
        self.borderwin.clear()
        self.borderwin.box(0, 0)
        
        self.w = FlexScrollColumn.FlexScrollColumn(self.borderwin, nlines,
                                                   maxwidth, 1, 1)

        self.curline = 0

        i = 0
        for d in list:
            if (str(d) == str(curr)):
                self.curline = i
                pass
            self.w.insertLine(i, (maxwidth,))
            self.w.setColumn(i, 0, str(d))
            i += 1
            pass

        self.w.highlightColumn(self.curline, 0)

        self.borderwin.refresh()
        return

    def handleChar(self, c):
        handled = False
        if (c == "ENTER"):
            self.done = True
            if (self.doneHandler):
                self.doneHandler(self.doneObj, self.list[self.curline])
                pass
            handled = True
            pass
        elif (c == "ESC" or c == "^C"):
            self.done = True
            if (self.doneHandler):
                self.doneHandler(self.doneObj, None)
                pass
            handled = True
            pass
        elif (c == "UP"):
            if (self.curline > 0):
                self.w.unhighlightColumn(self.curline, 0)
                self.curline -= 1
                self.w.highlightColumn(self.curline, 0)
                pass
            handled = True
            pass
        elif (c == "DOWN"):
            if (self.curline < self.nlines - 1):
                self.w.unhighlightColumn(self.curline, 0)
                self.curline += 1
                self.w.highlightColumn(self.curline, 0)
                pass
            handled = True
            pass
        self.w.refresh()
        return handled
    
    pass

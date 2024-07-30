#! /usr/bin/python
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
from . import DebugLog
from . import FlexScrollPad

# Break into lines that fit in the number of columns given
def _breakupLine(s, ncols):
    lines = []
    s = s.strip()
    while True:
        l = len(s)
        if (l <= ncols):
            if (l > 0):
                lines.append(s)
                pass
            
            break
        
        lastspace = 0
        i = 0
        for c in s:
            if (c.isspace()):
                lastspace = i
            elif (i >= ncols):
                if (lastspace > 0):
                    lines.append(s[0:lastspace])
                    s = s[lastspace:].strip()
                else:
                    lines.append(s[0:i])
                    s = s[i:].strip()
                    pass
                break
            i += 1
            pass
        pass
    return lines
    
if __name__== '__main__':
    l = _breakupLine("asdf asdf asdf asdf asdf asdf asdf", 10)
    print(l)

class Popup:
    # FIXME - If #line > displaylines, need to make this scrollable
    
    def __init__(self, parent, nlines, ncols, y, x, s, doneHandler=None,
                 doneObj=None, charHandler=None, reformat=True):
        self.done = False
        self.doneHandler = doneHandler
        self.doneObj = doneObj
        self.charHandler = charHandler
        
        if (reformat):
            # Get the actual number of lines needed and recalculate
            ilines = s.split("\n")
            lines = [ ]
            for l in ilines:
                lines = lines + _breakupLine(l, ncols - 2)
                pass
            pass
        else:
            lines = s.split("\n")
            pass
            
        wlines = nlines - 2
        if (len(lines) < wlines):
            y += (wlines / 2) - (len(lines) / 2)
            wlines = len(lines)
            nlines = wlines
            pass

        nlines += 2 # Add space for the border

        self.borderwin = parent.derwin(nlines, ncols, y, x)
        self.borderwin.clear()
        self.borderwin.box(0, 0)

        # Once we add the border, have to remove it from the display window.
        ncols -= 2
        nlines -= 2

        self.nlines = nlines

        self.w = FlexScrollPad.FlexScrollPad(self.borderwin,
                                             nlines, ncols, 1, 1)

        i = 0
        for d in lines:
            if (reformat and i >= nlines):
                break
            self.w.addstr(i, 0, d)
            i += 1
            pass
            
        self.borderwin.refresh()
        return

    def handleChar(self, c):
        if (self.charHandler is not None):
            self.done = self.charHandler(self.doneObj, c)
        elif (c == 'UP'):
            self.w.scrolly(-1)
            self.w.refresh()
        elif (c == 'DOWN'):
            self.w.scrolly(1)
            self.w.refresh()
        elif (c == 'NPAGE'):
            self.w.scrolly(self.nlines)
            self.w.refresh()
        elif (c == 'PPAGE'):
            self.w.scrolly(-self.nlines)
            self.w.refresh()
        elif (c == "ENTER"):
            self.done = True
            pass

        if (self.done):
            if (self.doneHandler):
                self.doneHandler(self.doneObj)
                pass
            pass

        return True
    
    pass

#
#    mvpartition - A disk partitions/RAID/LVM setup tool
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
import DebugLog

class PopupEditVals:
    # Create a popup window at the given y,x position, each line
    # holding a label and a value to edit.  The labels are all
    # right-justified, the value area to the right of that is where
    # the values are displayed.  valsize is the width of the values
    # area.  info is a sequence of sequences, the internal sequences
    # hold two things, the label to display and the initial value of
    # the given.
    def __init__(self, parent, y, x, info, valsize, doneHandler = None,
                 doneObj=None):
        self.done = False
        self.doneHandler = doneHandler
        self.doneObj = doneObj
        
        maxlabellen = 0
        nlines = 0
        for i in info:
            if (len(i[0]) > maxlabellen):
                maxlabellen = len(i[0])
                pass
            nlines += 1
            pass
        ncols = maxlabellen + 3 + valsize
        lines = []
        self.values = []
        for i in info:
            l = i[0]
            c = len(l)
            if (c < maxlabellen):
                l = "%*s%s" % (maxlabellen - c, " ", l)
                pass

            # Space extend it to the width to make editing easier.
            c = len(i[1])
            if (c < valsize):
                v = "%s%*s" % (i[1], valsize - c, " ")
            else:
                v = i[1][:valsize]
                pass
            self.values.append(v)
            lines.append(l + ": " + v)
            pass

        self.borderwin = parent.derwin(nlines + 2, ncols + 2, y, x)
        self.borderwin.clear()
        self.borderwin.box(0, 0)
        
        self.w = self.borderwin.derwin(nlines, ncols, 1, 1)

        i = 0
        for d in lines:
            self.w.addstr(i, 0, d)
            i += 1
            pass

        self.nlines = nlines
        self.valcol = maxlabellen + 2
        self.curline = 0
        self.curcol = 0
        self.valsize = valsize

        self._setCursor()
        
        self.borderwin.refresh()
        return

    def _setCursor(self):
        self.w.addstr(self.curline, self.curcol + self.valcol,
                      self.values[self.curline][self.curcol],
                      curses.A_STANDOUT)
        return
        
    def _clearCursor(self):
        self.w.addstr(self.curline, self.curcol + self.valcol,
                      self.values[self.curline][self.curcol],
                      0)
        return
        
    def handleChar(self, c):
        handled = False
        if (c == "ENTER"):
            self.done = True
            if (self.doneHandler):
                self.doneHandler(self.doneObj, self.values)
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
                self._clearCursor()
                self.curline -= 1
                self._setCursor()
                pass
            handled = True
            pass
        elif (c == "DOWN"):
            if (self.curline < self.nlines - 1):
                self._clearCursor()
                self.curline += 1
                self._setCursor()
                pass
            handled = True
            pass
        elif (c == "LEFT"):
            if (self.curcol > 0):
                self._clearCursor()
                self.curcol -= 1
                self._setCursor()
                pass
            handled = True
            pass
        elif (c == "RIGHT"):
            if (self.curcol < self.valsize - 1):
                self._clearCursor()
                self.curcol += 1
                self._setCursor()
                pass
            handled = True
            pass
        elif (c == "BACKSPACE" or c == "^H" or c == "DEL" or c == "DC"):
            if (c == "BACKSPACE" or c == "^H"):
                mincol = 1
            else:
                mincol = 0
                pass
            if (self.curcol >= mincol):
                self._clearCursor()

                if (c == "BACKSPACE" or c == "^H"):
                    self.curcol -= 1
                    pass

                v = self.values[self.curline]
                v = v[0:self.curcol] + v[self.curcol+1:] + " "
                self.values[self.curline] = v
                
                self.w.addstr(self.curline, self.curcol + self.valcol,
                              self.values[self.curline][self.curcol:],
                              0)

                self._setCursor()
                pass
            handled = True
            pass
        elif (len(c) == 1):
            # A normal character

            v = self.values[self.curline]
            v = v[0:self.curcol] + c + v[self.curcol+1:]
            self.values[self.curline] = v

            # _clearCursor will write the proper character out
            self._clearCursor()
            if (self.curcol < self.valsize - 1):
                self.curcol += 1
                pass
            self._setCursor()
            handled = True
            pass
        self.w.refresh()
        return handled
    
    pass

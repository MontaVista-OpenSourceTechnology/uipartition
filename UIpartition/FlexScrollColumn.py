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
from . import FlexScrollPad
from . import DebugLog

# FlexScrollColumn raises these when the user passes in bogus data.
class FlexScrollColumnErr(Exception):
    def __init__(self, str):
        self.s = str
        return

    def __str__(self):
        return self.s

    pass

LEFT = -1
RIGHT = 1

# Create a dummy object that will never be used, useful for comparisons.
class Dummy:
    pass
dummyObj = Dummy()

# A class that provides a line and column display and displays a window
# into the structure.  It consists of a list of lines.  Each line consists
# of a list of columns.
class FlexScrollColumn(FlexScrollPad.FlexScrollPad):
    # Create an object displayed in a windows that is nlines long and
    # ncols wide.  Those have nothing to do with the lines and columns
    # in the object, they are just the window size.  The upper left hand
    # corner of the window is given by the y and x positions.  This
    # is created as a child of the given parent and y and x are relative
    # to the parent.
    def __init__(self, parent, nlines, ncols, y, x):
        self.pad = FlexScrollPad.FlexScrollPad(parent, nlines, ncols,
                                               y, x)
        self.ncols = ncols
        self.colsizes = []
        self.cols = []
        self.objs = []
        return

    # Return the curses window
    def getWindow(self):
        return self.pad.getWindow()
    
    # How many lines are in the internal buffer?
    def numLines(self):
        return len(self.cols)

    # How many column are in the given line?
    def numColumns(self, line):
        return len(self.cols[line])

    def getNumDisplayLines(self):
        (lines, cols) = self.pad.getmaxyx()
        return lines

    # Return the displayed string for the given line/column
    def getField(self, line, column):
        return self.cols[line][column]

    # Return the object for the given line/column
    def getObj(self, line, column):
        return self.objs[line][column]
    
    # Set the object for the given line/column
    def setObj(self, line, column, obj):
        self.objs[line][column] = obj
        return

    def getFirstDisplayedLine(self):
        return self.pad.getFirstDisplayedLine()

    # Note: This may return more than the number of lines in the buffer if
    # only a partial screen is displayed at the end.
    def getLastDisplayedLine(self):
        return self.pad.getLastDisplayedLine()

    def scrolly(self, count=1):
        self.pad.scrolly(count)
        return
    
    # Push all changes to the display
    def refresh(self):
        self.pad.refresh()
        return

    # Redraw the window
    def redraw(self):
        self.pad.redraw()
        return

    # Return the line number that contains the given object in the given
    # column
    def lineOf(self, column, obj):
        for i in range(0, len(self.objs)):
            if (self.objs[i][column] == obj):
                return i
            pass
        raise FlexScrollColumnErr("Could not find object in list")

    # redo the columns to match the new colsizes, starting from the
    # given column
    def recolumn(self, line, col, colsizes):
        self.colsizes[line] = self.colsizes[line][0:col] + colsizes
        
        # Delete column information from col to the end:
        for i in range(col, len(self.cols[line])):
            del self.cols[line][col]
            del self.objs[line][col]
            pass

        # Add new empty column information
        for i in range(0, len(colsizes)):
            self.cols[line].append("")
            self.objs[line].append(None)
            pass

        # Redisplay the line, starting at the given column
        startx = 0
        for i in range(0, col):
            startx += self._getcolsize(line, i)
            pass

        self.pad.clrtoeol(line, startx)
        for i in range(col, len(self.cols[line])):
            self._showcol(line, i)
            pass
        return
        
    # Add a new line at the given line number.  The line number may be
    # one past the end to insert a new line at the end.  An array of
    # column sizes is provided, these are the string widths of each column
    # and the size of the array sets the number of columns in the inserted
    # line.  Note that each line has its own set of columns, and lines
    # may have different numbers of columns and column sizes.
    # If a column size is negative, that means the column is the given
    # width (without the minus) but it is not a selectable column, so
    # it will not be chosen for highlighting.  A zero length in the last
    # column means that it will take the rest of the line, and is
    # selectable.
    def insertLine(self, line, colsizes):
        if (line > len(self.cols)):
            raise FlexScrollColumnErr("insertLine at %d, last line was %d"
                                      % (line, len(self.cols)))

        self.colsizes.insert(line, colsizes)
        colstrs = [ ]
        objs = []
        for i in colsizes:
            colstrs.append("")
            objs.append(None)
            pass
        self.cols.insert(line, colstrs)
        self.objs.insert(line, objs)
        self.pad.insertln(line)
        self.pad.addstr(line, 0, "%*s" % (self.ncols, " "))
        return

    # Delete the given line
    def deleteLine(self, line):
        if (line >= len(self.cols)):
            raise FlexScrollColumnErr("deleteLine at %d, last line was %d"
                                      % (line, len(self.cols) - 1))

        del self.cols[line]
        del self.colsizes[line]
        del self.objs[line]
        self.pad.deleteln(line)
        return

    def _showcol(self, line, col, attr = 0):
        sizes = self.colsizes[line]
        s = self.cols[line][col]
        pos = 0
        for si in sizes[0:col]:
            if (si < 0):
                si = -si
                pass
            pos += si
            pass
        slen = len(s)
        size = sizes[col]
        if (size < 0):
            size = -size
        elif (size == 0): # Rest of line
            size = self.ncols - pos
            pass
        if (slen < size):
            # Pad end with spaces
            dstr = s + "%*s" % (size - slen, " ")
        elif (slen > size):
            dstr = s[0:size]
        else:
            dstr = s
            pass
        self.pad.addstr(line, pos, dstr, attr)
        return

    def _checklinepos(self, line, name):
        if (line >= len(self.cols)):
            raise FlexScrollColumnErr(name +" at %d, last line was %d"
                                      % (line, len(self.cols) - 1))
        return
        
    def _checkpos(self, line, col, name):
        self._checklinepos(line, name)
        if (col >= len(self.cols[line])):
            raise FlexScrollColumnErr(name +" at line %d, col %d, last"
                                      " col was %d"
                                      % (line, col, len(self.cols[line]) - 1))
        return

    def _getcolsize(self, line, col):
        c = self.colsizes[line][col]
        if (c < 0):
            c = -c
            pass
        return c

    # Set the text for a given column.  Note that if the text is wider
    # than the column width, only the first column width characters are
    # displayed.  If an object is provided it is set, too.
    def setColumn(self, line, col, s, obj=dummyObj, rjust=False):
        self._checkpos(line, col, "setColumn")
        colsize = self._getcolsize(line, col)
        if (rjust and len(s) < colsize):
            ladd = colsize - len(s)
            s = "%*s%s" % (ladd, " ", s)
            pass
        self.cols[line][col] = s
        if (obj != dummyObj):
            self.objs[line][col] = obj
            pass

        self._showcol(line, col)
        return

    # Highlight the given column, on the line, or the closest selectable
    # column available.  Dir is the preferred direction to move if the
    # given column is not available.  If no column is available in that
    # direction, the other direction is attempted.
    def highlightColumn(self, line, col, dir=RIGHT):
        self._checklinepos(line, "highlightColumn")
        sizes = self.colsizes[line]
        if (col >= len(sizes)):
            col = len(sizes) - 1
            pass
        if (col < 0):
            col = 0;
            pass
        found = False
        for i in (dir, -dir):
            if (found):
                break
            while (col >= 0 and col < len(sizes)):
                if (sizes[col] >= 0):
                    found = True
                    break
                col += i
                pass
            if (not found):
                col -= i
                pass
            pass
                
        if (not found):
            raise FlexScrollColumnErr("Unable to find column to highlight")
            
        self._showcol(line, col, curses.A_STANDOUT)
        return col

    # Remove highlighting from the given column.
    def unhighlightColumn(self, line, col):
        self._checkpos(line, col, "unhighlightColumn")
        sizes = self.colsizes[line]
        while (sizes[col] < 0):
            col += 1
            pass
        self._showcol(line, col, 0)
        return

    pass

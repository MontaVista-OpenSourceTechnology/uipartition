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

# FlexScrollPad raises these when the user passes in bogus data.
class FlexScrollPadErr(Exception):
    def __init__(self, str):
        self.s = str
        return

    def __str__(self):
        return self.s

    pass

# Extend sequence s by count items of to_add.  This should be built in
# to python :(
def _seqext(s, to_add, count):
    for i in range(0, count):
        s.append(to_add)
        pass
    return s

# flexscrollpad - a flexibled scrolled window
#
# This should exist and I shouldn't have to have written it, but it doesn't
# so I did.
#
# This is a window of a fixed size that displays a buffer.  If the buffer
# is larger than the window, then only part of the buffer is displayed.
# The buffer is variable sized and may be larger or smaller than the window,
# this code handles displaying it properly.
#
# For documenation, all these functions work just like the standard python
# curses functions, except they operate on the buffer, and not the window,
# and only affect the displayed text if something displayed is modified.
#
# A few things to note on this.  This has the concept of a current position,
# this is where adds, deletes, etc. occur by default.  If an operation is
# done on the current position, then the current position is generally moved
# to the end of the inserted item, or to the thing after the deleted item.
# When the buffer is changed, the current position stays at the same logical
# location.  So, for instance, if the current position is on line 10 and
# line 5 is deleted, the current position is moved to line 9, since that was
# the line that was at 10 before the delete.
#
# The y position must be within the given lines or an error is raised.
# The x position may be any positive number, though.
#
# The cursor is displayed at the current position, though the cursor's
# position is undefined if the current position is not in the displayed
# window.  In general, it's probably better to turn the cursor off.  This
# code would do that if curses had a per-window cursor, but the cursor off
# function is global in curses.  It would be fairly easy to implement a
# cursor in this code without using the hardware cursor.
#
# Adding a newline at the length of the buffer (one past the last line) causes
# a new line to be appended to the buffer.
#
# A newline in a string acts as if another addstring was done on the next
# line at column zero, including appending lines to the buffer.
#
# FIXME - this is not general purpose at the moment, it's missing some basic
# functions, but it would be easy to extend.
#
class FlexScrollPad:
    def __init__(self, parent, nlines, ncols, y, x):
        self.w = parent.derwin(nlines, ncols, y, x)
        self.nlines = nlines
        self.ncols = ncols
        self.curattr = 0
        self._clear()
        return

    # Return the curses window
    def getWindow(self):
        return self.w
    
    # Returns the current window line for the given buffer line, or -1
    # if the given buffer line is not currently displayed.
    def _displayposy(self, y):
        if (y < self.topy):
            return -1
        if (y > self.topy + self.nlines - 1):
            return -1
        return y - self.topy

    # Returns the current window line and column for the given buffer line
    # and column, or None if the given buffer pos (including anything in the
    # length) is not displayed.
    def _displaypos(self, y, x, xlen):
        dy = self._displayposy(y)
        if (dy == -1):
            return None
        if (x + xlen < self.topx):
            return None
        if (x > self.topx + self.ncols - 1):
            return None
        return (dy, x - self.topx)

    # Adding a character to the bottom right position in a
    # window results in an error, even though it actually
    # works.  This is apparently design intent, it it means
    # the cursor scrolled off the end of the screen :-(.  So
    # we work around this issue in an ugly fashion, as that's
    # something we have to do occactionally
    def _addstrHack(self, y, x, s, attr):
        if ((y == (self.nlines - 1)) and (x + len(s) == self.ncols)):
            dl = len(s)
            self.w.addstr(y, x, s[0:dl-1], attr)
            try:
                self.w.addch(y, x+dl-1, s[dl-1], attr)
            except:
                pass
            pass
        else:
            self.w.addstr(y, x, s, attr)
            pass
        return

    # Redisplay the entire given line.  This does not clear the line first,
    # and it doesn't do any error checking, the caller must do those things.
    def _redispLine(self, ywin):
        # First get the part of the line that is displayed.
        a = self.attr[self.topy + ywin]
        a = a[self.topx:self.topx + self.ncols]
        if (len(a) == 0):
            # Nothing to display, abort
            return
        b = self.buf[self.topy + ywin]
        b = b[self.topx:self.topx + self.ncols]
        
        ca = a[0]
        beg = 0
        for i in range(0, len(a)):
            if (a[i] != ca):
                self._addstrHack(ywin, beg, b[beg:i], ca)
                ca = a[i]
                beg = i
                pass
            pass
        # Don't care about the end, curses will chop it at the window edge.
        self._addstrHack(ywin, beg, b[beg:], ca)
        return

    # Redraw the whole window
    def redraw(self):
        self.w.clear()
        for y in range(0, self.nlines):
            if (y + self.topy >= len(self.buf)):
                break
            self._redispLine(y)
            pass
        return
        
    # Called to display any changes
    def refresh(self):
        pos = self._displaypos(self.cury, self.curx, 1)
        if (pos != None):
            self.w.move(pos[0], pos[1])
            pass
        self.w.refresh()
        return

    # Clear the entire buffer.
    def clear(self):
        self._clear()
        self.w.clear()
        return
    
    def _clear(self):
        self.buf = [ ]
        self.attr = [ ]
        self.topy = 0
        self.topx = 0
        self.cury = 0
        self.curx = 0
        return

    # Fetch parameters for functions that take an optional x, y
    def _getyxparms(self, args, name):
        if (len(args) == 0):
            y = self.cury
            x = self.curx
        elif (len(args) == 2):
            y = args[0]
            x = args[1]
        else:
            raise FlexScrollPadErr("Wrong number of arguments to " + name)

        if (y >= len(self.buf)):
            raise FlexScrollPadErr(name + " at %d, last line was %d"
                                   % (y, len(self.buf) - 1))
            pass

        return (y, x)
        
    # Process parameters for addstr and insstr, ([y, x,] str [, attr])
    def _getaddinsparms(self, args, name):
        atcursor = False
        if (len(args) == 1):
            atcursor = True
            y = self.cury
            x = self.curx
            s = args[0]
            attr = self.curattr
        elif (len(args) == 2):
            atcursor = True
            y = self.cury
            x = self.curx
            s = args[0]
            attr = args[1]
        elif (len(args) == 3):
            y = args[0]
            x = args[1]
            s = args[2]
            attr = self.curattr
        elif (len(args) == 4):
            y = args[0]
            x = args[1]
            s = args[2]
            attr = args[3]
        else:
            raise FlexScrollPadErr("Wrong number of arguments to " + name)

        if (y > len(self.buf)):
            raise FlexScrollPadErr(name + " at %d, last line was %d"
                                   % (y, len(self.buf)))
            pass

        return (y, x, s, attr, atcursor)

    # Modify the attributes of the given line/characters.  The current
    # position is not moved.  Setting xlen to -1 means to the end of the line
    def modattr(self, attr, y = None, x = None, xlen = 1):
        if (y == None):
            y = self.cury
            pass
        if (x == None):
            x = self.curx
            pass
        if (xlen == -1):
            xlen = self.ncols
            pass
        
        if (y >= len(self.buf)):
            raise FlexScrollPadErr("modattr at %d, last line was %d"
                                   % (y, len(self.buf) - 1))
            pass

        a = self.attr[y]
        alen = len(a)
        if (x >= alen):
            return
        elif (x + xlen > alen):
            xlen = alen - x
            pass

        s = self.buf[y][x:x + xlen]
        for i in range(x, x + xlen):
            a[i] = attr
            pass

        dp = self._displaypos(y, x, xlen)
        if (dp != None):
            dy = dp[0]
            dx = dp[1]
            if (dx < self.topx):
                # Starts to the left of the screen, chop off the beginning.
                dstr = s[self.topx - dx:]
                dx = 0
            else:
                # Starts in the screen.  Let curses chop of the right if
                # required.
                dstr = s
                pass
            self._addstrHack(dy, dx, dstr, attr)
            pass

        return
    
    # Add a string at the given position, overwriting anything that
    # was there.  If only the string is given, the string is added at
    # the current position and the cursor is moved past the end of the
    # newly added string.
    # If newlines are in the string, they will cause it to go to the
    # next line and write over.  This will append lines to the buffer
    # if it hits the end of the buffer.
    def addstr(self, *args):
        (y, x, s, attr, atcursor) = self._getaddinsparms(args, "insstr")

        # Take each line individually and add it to the buffer, inserting
        # lines as necessary.
        p = 0
        while (p != -1):
            if (y >= len(self.buf)):
                self.insertln(y)
                pass
            n = s.find("\n", p)
            if (n == -1):
                self._addstr(y, x, s[p:], attr)
                slen = len(s) - p
                p = -1
            else:
                self._addstr(y, x, s[p:n], attr)
                p = n + 1
                y += 1
                x = 0
                pass
            pass

        if (atcursor):
            self.curx = x + slen
            self.cury = y
            pass

        return

    def _addstr(self, y, x, s, attr):
        # Now put the string into the buffer, overwriting any contents
        # that was at the position.
        sb = self.buf[y]
        sa = self.attr[y]
        sblen = len(sb)
        slen = len(s)
        a = _seqext([], attr, slen);
        if (x < sblen):
            # Inserting starting inside the current string
            epos = x + slen
            if (epos < sblen):
                # Fully inside the current string
                sb = sb[0:x] + s + sb[epos:]
                sa = sa[0:x] + a + sa[epos:]
            else:
                # Starts inside, but goes past the end
                sb = sb[0:x] + s
                sa = sa[0:x] + a
                pass
            pass
        elif (x > sblen):
            # Past the end of the current string, pad with spaces
            sb = sb + ("%*s" % (x - sblen, " ")) + s
            sa = sa + _seqext([], self.curattr, x - sblen) + a
        else:
            # Right at the end of the current string, easy.
            sb = sb + s
            sa = sa + a
            pass
        self.buf[y] = sb
        self.attr[y] = sa

        # If the inserted string is currently displayed, update the
        # window.
        dp = self._displaypos(y, x, slen)
        if (dp != None):
            dy = dp[0]
            dx = dp[1]
            if (dx < self.topx):
                # Starts to the left of the screen, chop off the beginning.
                dstr = s[self.topx - dx:]
                dx = 0
            else:
                # Starts in the screen.  Chop off the end if necessary
                dstr = s[:dx - self.topx + self.ncols]
                pass
            
            self._addstrHack(dy, dx, dstr, attr)
        return

    # Insert a line at the given line.  If no argument was given, it is done
    # at the cursor and the cursor is moved.
    def insertln(self, *args):
        atcursor = False
        if (len(args) == 0):
            atcursor = True
            y = self.cury
        elif (len(args) == 1):
            y = args[0]
        else:
            raise FlexScrollPadErr("Wrong number of arguments to insertln")

        if (y > len(self.buf)):
            raise FlexScrollPadErr("insertln at %d, last line was %d"
                                   % (y, len(self.buf)))
            pass

        self.buf.insert(y, "")
        self.attr.insert(y, [])
        py = self._displayposy(y)
        if (py >= 0):
            # Inserted position was displayed, update the window.
            self.w.move(py, 0)
            self.w.insertln()
            pass

        if (self.cury > y and not atcursor):
            # Insert was above current position, adjust.
            self.cury += 1
            pass

        if (self.topy > y):
            self.topy += 1
            pass
        
        return

    # Delete the given line, or the current line if no line is given.
    # The current position is not moved if this is done at the cursor.
    # line.
    def deleteln(self, *args):
        if (len(args) == 0):
            y = self.cury
        elif (len(args) == 1):
            y = args[0]
        else:
            raise FlexScrollPadErr("Wrong number of arguments to deleteln")

        if (y >= len(self.buf)):
            raise FlexScrollPadErr("deleteln at %d, last line was %d"
                                   % (y, len(self.buf) - 1))
            pass

        del self.buf[y]
        del self.attr[y]
        
        py = self._displayposy(y)
        if (py >= 0):
            # Deleted position was displayed, update the window.
            self.w.move(py, 0)
            self.w.deleteln()
            if (self.topy + self.nlines < len(self.buf)):
                # Lines past the bottom of the window, display the bottom line
                self._redispLine(self.nlines - 1)
                pass
            pass

        if (self.topy >= len(self.buf)):
            # Deleted the last line, and it was on the top position of the
            # window, have to adjust the top by moving to the previous line.
            if (self.topy > 0):
                if (self.topy == self.cury):
                    # Current was the last line, move it up one.
                    self.cury -= 1
                    pass
                self.topy -= 1
                self._redispLine(0)
                pass
            pass
        else:
            if (self.cury > y):
                # Delete was above current position, adjust.
                self.cury -= 1
                pass
            if (self.topy > y):
                self.topy -= 1
                pass
            pass

        return

    # Scroll, negative to scroll down, positive to scroll up, by the given
    # number of lines.  Current position is not changed.
    def scrolly(self, count = 1):
        if (count < 0):
            # Scrolling down
            count = -count
            if (self.topy == 0):
                return
            
            if (count > self.topy):
                count = self.topy
                pass
                
            self.topy -= count
            if (count >= self.nlines):
                # Scrolling a screen or more, just redraw
                self.redraw()
            else:
                # Partial scroll, optimize with inserts and adds
                self.w.move(0, 0)
                for y in range(0, count):
                    self.w.insertln()
                    pass
                for y in range(0, count):
                    self._redispLine(y)
                    pass
                pass
            pass
        elif (count > 0):
            # scrolling up
            if (self.topy + self.nlines >= len(self.buf)):
                return

            if (self.topy + count > len(self.buf)):
                count = len(self.buf) - self.topy - 1
                pass

            self.topy += count
            if (count >= self.nlines):
                # Scrolling a screen or more, just redraw
                self.redraw()
            else:
                # Partial scroll, optimize with deletes and adds
                self.w.move(0, count)
                for y in range(0, count):
                    self.w.deleteln()
                    pass
                for y in range(self.nlines - count - 1, self.nlines):
                    self._redispLine(y)
                    pass
                pass
            pass
        return

    # Scroll horizontally, positive numbers scroll left, negative numbers
    # scroll right.
    # FIXME - this could be more optimal.
    def scrollx(self, count = 1):
        if (count == 0):
            return
        if (self.topx + count < 0):
            self.topx = 0
        else:
            self.topx += count
            pass
        self.redraw()
        return
    
    # Get the Y and X offset of the top-left corner of the window
    def getbegyx(self):
        return (self.topy, self.topx)

    # Get the number of lines and columns in the window
    def getmaxyx(self):
        return (self.nlines, self.ncols)

    def getFirstDisplayedLine(self):
        return self.topy

    # Note: This may return more than the number of lines in the buffer if
    # only a partial screen is displayed at the end.
    def getLastDisplayedLine(self):
        return self.topy + self.nlines - 1

    # Get the current cursor (y, x) position
    def getxy(self):
        return (self.cury, self.curx)

    # Move the cursor to the given position
    def move(self, y, x):
        if (y >= len(self.buf)):
            raise FlexScrollPadErr("move to line %d, last line was %d"
                                   % (y, len(self.buf) - 1))
            pass

        self.cury = y
        self.curx = x
        return

    # Insert a string at the given location.
    def insstr(self, *args):
        (y, x, s, attr, atcursor) = self._getaddinsparms(args, "insstr")
        orig_y = y
        orig_x = x

        if (y >= len(self.buf)):
            # Inserting to the end is the same as adding to the end
            self.addstr(y, x, s, attr)
            return

        # Get whatever follows after the insert
        b = self.buf[y]
        a = self.attr[y]
        afterb = b[x:]
        aftera = a[x:]
        self.clrtoeol(y, x)

        # Make room for the string and call addstr
        nlcount = s.count("\n")
        for i in range(0, nlcount):
            self.insertln(y + 1)
            pass
        self.addstr(y, x, s, attr)

        # Now insert the cut string, making sure to get the attributes right
        if (len(a) > 0):
            y += nlcount
            x = len(self.buf[y])
            new_x = x
            ca = aftera[0]
            beg = 0
            for i in range(1, len(aftera)):
                if (aftera[i] != ca):
                    self._addstr(y, x + beg, afterb[beg:i], ca)
                    ca = aftera[i]
                    beg = i
                    pass
                pass
            self._addstr(y, x + beg, afterb[beg:], ca)
            pass

        self.refresh()
        curses.napms(1000)

        if (atcursor):
            self.cury = y
            self.curx = x
            pass
        elif (self.cury > orig_y):
            self.cury += nlcount
        elif (self.cury == orig_y and self.curx >= orig_x):
            self.cury += nlcount
            self.curx = self.curx - orig_x + new_x
            pass
        return

    # Delete a character at the given y,x position, or at the current position
    # if not specified.
    def delch(self, *args):
        (y, x) = self._getyxparms(args, "delch")

        b = self.buf[y]
        if (x >= len(b)):
            return
        a = self.attr[y]

        self.buf[y] = b[0:x] + b[x+1:]
        self.attr[y] = a[0:x] + a[x+1:]

        dp = self._displaypos(y, x, 1)
        if (dp != None):
            self.w.delch(dp[0], dp[1])
            pass
        return

    # Clear to end of line at the given y,x position, or at the
    # current position if not specified.
    def clrtoeol(self, *args):
        (y, x) = self._getyxparms(args, "clrtoeol")

        b = self.buf[y]
        slen = len(b) - x
        if (slen <= 0):
            return
        
        a = self.attr[y]
        self.buf[y] = b[0:x]
        self.attr[y] = a[0:x]

        dp = self._displaypos(y, x, slen)
        if (dp != None):
            dy = dp[0]
            dx = dp[1]
            if (dx < self.topx):
                # Starts to the left of the screen, chop off the beginning.
                dx = 0
                pass
            self.w.move(dy, dx)
            self.w.clrtoeol()
            pass
        return

    # Set the current default attribute.
    def attrset(self, attr):
        self.curattr = attr
        self.w.attrset(attr)
        return
    
    pass

def testWindow(stdscr):
    curses.curs_set(0) # Turn off the cursor

    (wheight, wwidth) = stdscr.getmaxyx()

    fpheight = wheight - 4

    p = stdscr.derwin(10, 20, 5, 5)
    (wheight, wwidth) = p.getmaxyx()

    fpheight = wheight - 4

    fpad = FlexScrollPad(p, fpheight, wwidth, 2, 0)
    fpad.addstr("Hello there\n")
    fpad.addstr("How are you?\n")
    fpad.refresh()
    curses.napms(500)

    for i in range(0, 25):
        fpad.addstr(str(i) + "\n")
        fpad.refresh()
        curses.napms(100)
        pass
    fpad.deleteln(27)

    fpad.scrolly(-1)
    fpad.refresh()
    curses.napms(300)
    for i in range(0, 10):
        fpad.scrolly()
        fpad.refresh()
        curses.napms(100)
        pass
    fpad.deleteln(25)
    fpad.refresh()
    curses.napms(300)
    fpad.insertln(25)
    fpad.addstr(25, 0, "23-2")
    fpad.addstr(25, 20, "at  end")
    fpad.refresh()
    curses.napms(300)
    fpad.insstr(25, 23, "the", curses.A_BOLD)
    fpad.refresh()
    curses.napms(300)
    fpad.addstr(25, 29, "d of line", curses.A_STANDOUT)
    fpad.refresh()
    curses.napms(300)

    fpad.delch(25, 23)
    fpad.refresh()
    curses.napms(300)
    
    fpad.clrtoeol(25, 34)
    fpad.refresh()
    curses.napms(500)
    
    for i in range(0, 10):
        fpad.scrolly(-1)
        fpad.refresh()
        curses.napms(100)
        pass
    fpad.scrolly(fpheight)
    fpad.refresh()
    curses.napms(300)
    for i in range(0, 10):
        fpad.scrolly(-1)
        fpad.refresh()
        curses.napms(100)
        pass
    fpad.scrolly(-fpheight)
    fpad.refresh()
    curses.napms(300)

    fpad.scrollx(2)
    fpad.refresh()
    curses.napms(300)
    fpad.scrollx(-2)
    fpad.refresh()
    curses.napms(300)

    fpad.scrolly(4)
    fpad.refresh()
    curses.napms(300)
    fpad.scrolly(fpheight)
    fpad.refresh()
    curses.napms(300)
    fpad.deleteln(23)
    fpad.refresh()
    curses.napms(300)
    fpad.deleteln(25)
    fpad.refresh()
    curses.napms(300)
    fpad.insstr(24, 1, "hello\nthere\n")
    fpad.refresh()
    curses.napms(1000)
    for i in range(24, -1, -1):
        fpad.deleteln(i)
        fpad.refresh()
        curses.napms(300)
        pass
    fpad.deleteln(0)
    fpad.refresh()
    curses.napms(300)
    fpad.deleteln(0)
    fpad.refresh()
    curses.napms(300)
    
    curses.napms(1000)
    return

if __name__== '__main__':
    curses.wrapper(testWindow)

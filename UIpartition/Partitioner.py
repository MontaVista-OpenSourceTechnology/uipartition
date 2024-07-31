#!/usr/bin/python
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

# TODO:
#
#   It would be nice if menus came down under the appropriate place and
#   not just at the left-hand side of the screen.
#

# DebugLog.debuglog._log("A: ")

import curses
from . import FlexScrollPad
from . import FlexScrollColumn
from . import CursesKeyMap
from . import Popup
from . import PopupEditVals
from . import PopupList
import copy
from . import DebugLog
import subprocess
import tempfile
import os

import sys
import traceback
import json

# Dummy object for passing around info
class Obj:
    pass

# FIXME - circular references between disks and partitions, disks and
# the partitioner, etc.  Make sure to remove them on deletes

class PartitionerErr(Exception):
    def __init__(self, str):
        self.s = str
        return

    def __str__(self):
        return self.s

    pass

# Sizes for various fields
_namelen = 15 # Including indention
_sizelen = 13 # A size or start/end location, good for 100+ terabytes of sectors
_typelen = 6  # filesystem/partition type

class CmdErr(Exception):
    def __init__(self, cmd, returncode, out, errout):
        self.cmd = cmd
        self.returncode = returncode
        self.out = out
        self.errout = errout
        return

    def __str__(self):
        return ("Error %d:%s\n%s\n%s"
                % (self.returncode, self.cmd, self.out, self.errout))

    pass

def _call_cmd(cmd):
    prog = subprocess.Popen(cmd,
                            stdin=None, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, close_fds=True)
    (out, err) = prog.communicate()
    out = out.decode("utf8")
    err = err.decode("utf8")
    if (prog.returncode != 0):
        raise CmdErr(str(cmd), prog.returncode, out, err)
    return out

# Default unit is sectors
def _call_parted(dev, cmds, unit="s"):
    return _call_cmd(["parted", "-msj", "--align=none", dev, "unit " + unit]
                     + cmds)
    
def _call_mdadm(dev, cmd, opts=[]):
    return _call_cmd(["mdadm", cmd, dev] + opts)

def _call_lvmcmd(cmd, opts = []):
    return _call_cmd([cmd, ] + opts)

def _call_lvmdispcmd(cmd, opts = []):
    opts = ["--units", "s", "--noheadings", "--nosuffix"] + opts
    return _call_lvmcmd(cmd, opts)

def _reread_partition_table(devname):
    return _call_cmd(["blockdev", "--rereadpt", devname])

def _get_file_info(devname):
    return _call_cmd(("file", "--special-files", "--dereference", devname))

def _get_dev_uuid(devname):
    try:
        uuid = _call_cmd(("blkid", "-o", "value", "-s", "UUID", 
                          devname)).strip()
    except CmdErr:
        uuid = None
        pass
    return uuid
    
#
# A superclass for unit display
#
class Units:
    def convToStr(self, device, val):
        val = val * device.sectsize
        if (self.divider < 10000):
            s = str(val / self.divider) + self.unitdisplay
        else:
            s = (str(int(val / self.divider))
                 + '.' + ("%2.2d" % (val / (self.divider / 100)))[-2:]
                 + self.unitdisplay)
            pass
        return s

    def convFromStr(self, p, device, s):
        s = s.strip()
        if (s[-1] == self.unitdisplay):
            s = s[0:-1]
            pass
        v = int(float(s) * self.divider) / device.sectsize
        return device.alignValue(p, v)
    
    pass

class SectorUnits(Units):
    name = "S"

    def convToStr(self, device, val):
        return str(val)

    def convFromStr(self, p, device, s):
        return int(s)
    
    def allocNext(self):
        return KiBUnits()

    pass

# FIXME - is Ki units really that useful?
class KiBUnits(Units):
    name = "Ki"
    unitdisplay = "K"
    divider = 1024

    def allocNext(self):
        return MiBUnits()

    pass

class MiBUnits(Units):
    name = "Mi"
    unitdisplay = "M"
    divider = 1024000

    def allocNext(self):
        return GiBUnits()

class GiBUnits(Units):
    name = "Gi"
    unitdisplay = "G"
    divider = 1024000000

    def allocNext(self):
        return SectorUnits()

    pass

#
# A superclass for all destination values
#
class DestValue:

    def needsWork(self, p, device):
        return ()

    pass

#
# A superclass for all destination subtypes
#
class DestSubtype:

    def needsWork(self, p, device):
        return ()

    pass

class WorkObj:
    def __init__(self, obj, data, write_op=False):
        self.obj = obj
        self.data = data
        self.write_op = write_op
        return

    def work(self, p):
        self.obj.work(p, self.data)
        return

    pass

#
# A superclass for various filesystem types, also used for unset filesystem
#
class FSType(DestSubtype):
    name = ""
    file_str = "no match here"
    opts = [ ]

    def __init__(self):
        return

    def __str__(self):
        return self.name

    def match(self, s):
        return self.file_str in s

    def setup(self, parent, p, line, col):
        self.parent = parent
        self.col = col

        p.setObj(line, col, self)
        p.setColumn(line, col, str(self))
        return

    def Command(self, p, c):
        if (c == "ENTER"):
            o = Obj()
            o.p = p
            p.popup = PopupList.PopupList(p.getWindow(), 4, 0,
                                          _fs_types, self.name,
                                          self.selectFSDone, o)
            return True
        return False

    def selectFSDone(self, o, val):
        p = o.p
        if ((val is None) or (val.__class__ == self.__class__)):
            # Unchanged
            return

        val = val.newInst()
        val.parent = self.parent
        val.col = self.col

        # Setup the new type
        self.parent.subtype = val
        self.parent.changed = True

        p.setObj(p.linepos, self.col, val)
        p.setColumn(p.linepos, self.col, str(val))

        p.redoHighlight()
        return

    def needsWork(self, p, device):
        if (not self.name or not self.parent.changed or p.in_reread):
            # Don't do the empty fstype or if unchanged
            return ()
        return (WorkObj(self, device, write_op=True), )

    def work(self, p, device):
        p.redraw()
        p.popupInfo("Making %s filesystem on %s" % (self.name, device.devname))

        # Make sure the existing filesystem check doesn't fail the mkfs
        _call_cmd(["dd", "if=/dev/zero", "of=" + device.devname, "count=100"])
        _call_cmd(["mkfs." + self.name,] + self.opts + [device.devname, ])
        return

    def newInst(self):
        return FSType()

    pass

class Ext2FS(FSType):
    name = "ext2"
    file_str = "ext2 filesystem"
    
    def newInst(self):
        return Ext2FS()

    pass

class Ext3FS(FSType):
    name = "ext3"
    file_str = "ext3 filesystem"
    
    def newInst(self):
        return Ext3FS()

    pass

class Ext4FS(FSType):
    name = "ext4"
    file_str = "ext4 filesystem"
    
    def newInst(self):
        return Ext4FS()

    pass

class XFSFS(FSType):
    name = "xfs"
    file_str = "XFS filesystem"
    opts = [ "-f", ]
    
    def newInst(self):
        return XFSFS()

    pass

class VFATFS(FSType):
    name = "vfat"
    file_str = "mkdosfs"
    
    def newInst(self):
        return VFATFS()

    pass

_fs_types = [ FSType(), Ext4FS(), Ext3FS(), Ext2FS(), XFSFS(), VFATFS() ]

def _valid_filesystem(f):
    for t in _fs_types[1:]:
        if (f == str(t)):
            return t.newInst()
        pass
    return None

class MountPoint(DestValue):
    def __init__(self, value=""):
        self.value = value
        return
    
    def __str__(self):
        return self.value

    def setup(self, parent, p, line, col):
        self.parent = parent
        self.col = col

        p.setObj(line, col, self)
        p.setColumn(line, col, str(self))
        return

    def Command(self, p, c):
        if (c == "ENTER"):
            o = Obj()
            o.p = p
            p.popup = PopupEditVals.PopupEditVals(
                p.getWindow(), 4, 0,
                (("Mount Point", str(self)),),
                50, self.editStringobjDone, o)
            return True
        return False

    def editStringobjDone(self, o, vals):
        p = o.p
        if ((vals is None) or (vals[0] == str(self))):
            # Unchanged
            return

        self.value = vals[0].strip()
        p.setColumn(p.linepos, self.col, str(vals[0]))

        p.redoHighlight()
        return

    def clearVal(self, p, line):
        self.value = ""
        p.setColumn(line, self.col, self.value)
        return
    pass

#
# A superclass for all partition/LVM/RAID targets.
#
class DestType:
    def __init__(self, subtype = None, value = None):
        self.subtype = subtype
        self.value = value
        return

    def __str__(self):
        return self.desttype

    def destType(self):
        return self.desttype

    def setup(self, parent, p, line, col):
        self.parent = parent
        self.col = col

        p.recolumn(line, col, self.columns)
        p.setObj(line, col, self)
        p.setColumn(line, col, str(self))
        col += 1
        if (self.subtype is not None):
            self.subtype.setup(self, p, line, col)
            col += 1
            pass
        if (self.value is not None):
            self.value.setup(self, p, line, col)
            col += 1
            pass
        return

    def shutdown(self, p):
        return

    # Called when the value is modified
    def modified(self):
        return
        
    def Command(self, p, c):
        if (c == "ENTER"):
            o = Obj()
            o.p = p
            p.popup = PopupList.PopupList(p.getWindow(), 4, 0,
                                          self.parent.allowed_dests,
                                          self.desttype,
                                          self.selectDestDone, o)
            return True
        return False

    def selectDestDone(self, o, val):
        p = o.p
        if ((val is None) or (val == self.desttype)):
            # Unchanged
            return

        dest = _alloc_dest(val)
        self.newDest(p, dest, p.linepos)

        # Convert over to the new destination
        dest.modified()

        p.redoHighlight()
        return

    def newDest(self, p, dest, line, doshutdown=True):
        if (doshutdown):
            # Remove it from whatever it's part of.
            self.shutdown(p)
            pass
        
        # Setup the new type
        self.parent.dest = dest
        dest.setup(self.parent, p, line, self.col)

        # Break circular references so we are really deleted
        del self.parent
        del self.subtype
        del self.value
        return

    def clearVal(self, p, line):
        self.value.clearVal(p, line)
        return

    def needsWork(self, p, device):
        if (self.subtype):
            return self.subtype.needsWork(p, device)
        if (self.value):
            return self.value.needsWork(p, device)
        return ()

    pass

class FSDest(DestType):
    desttype = "fs"
    columns = (_typelen, _typelen, 0)
    
    def __init__(self, subtype = None, value = None, do_init=True,
                 options="defaults", dump="0", passnum=None):
        self.changed = do_init
        self.options = options
        self.dump = dump
        self.passnum = passnum
        if (subtype == None):
            subtype = FSType()
            pass
        if (value is None):
            value = MountPoint()
            pass
        DestType.__init__(self, subtype, value)
        return

    def needsWork(self, p, device):
        work = DestType.needsWork(self, p, device)
        if (str(self.value) and self.subtype.name and p.output_fstab_str):
            return work + (WorkObj(self, device), )
        return work

    def work(self, p, device):
        mountpoint = str(self.value).strip()
        if (self.passnum is not None):
            passnum = self.passnum
        elif (mountpoint == '/'):
            passnum = "1"
        else:
            passnum = "2"
            pass
        p.output_fstab.write(device.getMountName() + "\t" + mountpoint + "\t"
                             + self.subtype.name + "\t" + self.options
                             + "\t" + self.dump + "\t" + passnum + "\n")
        return

    def modified(self):
        self.changed = True
        self.parent.set("lvm off")
        self.parent.set("raid off")
        self.parent.set("swap off")
        return
        
    def newInst(self, do_init=True):
        return FSDest(do_init=do_init)

    pass

class RAIDValue(DestValue):
    def __init__(self, raid=None):
        self.raid = raid
        return

    def __str__(self):
        if (self.raid):
            return self.raid.devname
        return ""

    def setup(self, parent, p, line, col):
        self.parent = parent
        self.col = col
        p.setObj(line, col, self)
        if (self.raid):
            p.setColumn(line, col, self.raid.devname)
            pass
        return

    def setRAID(self, p, line, raid):
        self.raid = raid
        if (self.raid):
            p.setColumn(line, self.col, self.raid.devname)
            pass
        return

    def shutdown(self, p):
        # Break circular dependencies
        self.removeFromRaid(p)
        del self.parent
        return

    def Command(self, p, c):
        if (c == "ENTER"):
            if (len(p.raids) == 0):
                raise PartitionerErr("No RAID devices defined.")
            o = Obj()
            o.p = p
            if (self.raid):
                currdevname = self.raid.devname
            else:
                currdevname = ""
                pass
            p.popup = PopupList.PopupList(p.getWindow(), 4, 0,
                                          ["",] + p.raids, currdevname,
                                          self.selectRAIDDone, o)
            return True
        return False

    def selectRAIDDone(self, o, val):
        p = o.p
        if ((val is None) or ((self.raid != None) and (val == str(self.raid)))):
            # Unchanged
            return

        if (self.raid):
            self.raid.removeVol(p, self.parent.parent)
            pass

        if (val):
            # Setup the new type.  Strange order is so self.raid doesn't get
            # set if addVol throws and exception.
            newraid = p.findObj(val)
            newraid.addVol(p, self.parent.parent)
            self.raid = newraid
        else:
            self.raid = None
            pass

        p.setColumn(p.linepos, self.col, str(val))

        p.redoHighlight()
        return

    def removeFromRaid(self, p):
        if (self.raid):
            self.raid.removeVol(p, self.parent.parent)
            self.raid = None
            pass
        return

    def clearVal(self, p, line):
        self.removeFromRaid(p)
        p.setColumn(line, self.col, "")
        return
    
    pass

class RAIDDest(DestType):
    desttype = "RAID"
    columns = (_typelen, 0)
    
    def __init__(self, raid=None, do_init=True):
        if (raid is None):
            raid = RAIDValue()
            pass
        DestType.__init__(self, value=raid)
        return

    def shutdown(self, p):
        DestType.shutdown(self, p)
        self.value.shutdown(p)
        return

    def setRAID(self, p, line, raid):
        self.value.setRAID(p, line, raid)
        return

    def modified(self):
        self.parent.set("raid on")
        return

    def newInst(self, do_init=True):
        return RAIDDest(do_init=do_init)
    
    pass

class LVMValue(DestValue):
    def __init__(self, vg=None, do_init=True):
        self.vg = vg
        self.do_init = do_init
        return

    def __str__(self):
        if (self.vg):
            return self.vg.devname
        return ""

    def setup(self, parent, p, line, col):
        self.parent = parent
        self.col = col
        p.setObj(line, col, self)
        if (self.do_init):
            _call_lvmcmd("pvcreate", ["-ff", "-y", self.parent.parent.devname])
            self.do_init = False
            pass
        if (self.vg):
            p.setColumn(line, col, self.vg.devname)
            pass
        return

    def setVG(self, p, line, vg):
        self.vg = vg
        if (self.vg):
            p.setColumn(line, self.col, self.vg.devname)
            pass
        return

    def shutdown(self, p):
        self.removeFromVG(p)
        _call_lvmcmd("pvremove", [self.parent.parent.devname,])

        # Break circular dependencies
        del self.parent
        return

    def Command(self, p, c):
        if (c == "ENTER"):
            if (len(p.vgs) == 0):
                raise PartitionerErr("No LVM devices defined.")
            o = Obj()
            o.p = p
            if (self.vg):
                currdevname = self.vg.devname
            else:
                currdevname = ""
                pass
            p.popup = PopupList.PopupList(p.getWindow(), 4, 0,
                                          ["",] + p.vgs, currdevname,
                                          self.selectLVMDone, o)
            return True
        return False

    def selectLVMDone(self, o, val):
        p = o.p
        if ((val is None) or ((self.vg != None) and (val == str(self.vg)))):
            # Unchanged
            return

        if (self.vg):
            self.vg.removePVol(self.parent.parent, p)
            pass

        if (val):
            # Setup the new type
            self.vg = p.findObj(val)
            self.vg.addPVol(self.parent.parent, p)
        else:
            self.vg = None
            pass

        p.setColumn(p.linepos, self.col, str(val))

        p.redoHighlight()
        return

    def removeFromVG(self, p):
        if (self.vg):
            self.vg.removePVol(self.parent.parent, p)
            self.vg = None
            pass
        return

    def clearVal(self, p, line):
        self.removeFromVG(p)
        p.setColumn(line, self.col, "")
        return
    
    pass

class LVMDest(DestType):
    desttype = "LVM"
    columns = (_typelen, 0)
    
    def __init__(self, value = None, do_init=True):
        if (value is None):
            value = LVMValue(do_init=do_init)
            pass
        DestType.__init__(self, value = value)
        return
    
    def shutdown(self, p):
        DestType.shutdown(self, p)
        self.value.shutdown(p)
        return

    def modified(self):
        self.parent.set("lvm on")
        return

    def setVG(self, p, line, vg):
        self.value.setVG(p, line, vg)
        return

    def newInst(self, do_init=True):
        return LVMDest(do_init=do_init)
    
    pass

class SwapDest(DestType):
    desttype = "swap"
    columns = (_typelen,)
    
    def __init__(self, do_init=True):
        DestType.__init__(self)
        self.changed = False
        return
    
    def modified(self):
        self.changed = True
        self.parent.set("swap on")
        return

    def needsWork(self, p, device):
        if (self.changed or p.output_fstab_str):
            return (WorkObj(self, device, write_op=self.changed), )
        return ()

    def work(self, p, device):
        if (p.output_fstab_str):
            p.output_fstab.write(device.getMountName()
                                 + "\tnone\tswap\tsw\t0\t0\n")
            pass
        if (self.changed):
            p.redraw()
            p.popupInfo("Making swap filesystem on %s" % (device.devname))
            # Can't use _call_cmd because of the popupInfoDone
            cmd = ("mkswap", "-f", device.devname)
            prog = subprocess.Popen(cmd,
                                    stdin=None, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, close_fds=True)
            (out, err) = prog.communicate()
            p.popupInfoDone()
            if (prog.returncode != 0):
                raise CmdErr(str(cmd), prog.returncode, out, err)
            pass
        return

    def newInst(self, do_init=True):
        return SwapDest(do_init=do_init)
    
    pass

# For things that can be partitioned
class PartitionDest(DestType):
    desttype = "part"
    columns = (_typelen, _typelen, -6, -_sizelen)

    def __init__(self, table=None, do_init=True):
        self.do_init = do_init
        if (table is None):
            table = InvalidPartitionTable()
            pass
        DestType.__init__(self, value=table)
        return

    def setup(self, parent, p, line, col):
        self.devname = parent.devname
        # The device for a partition table defaults to the parent, which would
        # be this object in this case.  Override it to the proper device.
        self.value.setDevice(parent)
        if (self.do_init and parent.running):
            self.value.write(self.devname)
            self.do_init = False
            pass
        DestType.setup(self, parent, p, line, col)
        parent.partitionInit(p, line, self.value)
        return

    def clearPartitions(self, p, newtab):
        self.parent.clearPartitions(p, newtab)
        return

    def shutdown(self, p):
        DestType.shutdown(self, p)
        self.value.shutdown(p)
        self.parent.partitionShutdown()
        return

    def modified(self):
        return

    def newInst(self, do_init=True):
        return PartitionDest(do_init=do_init)
    
    pass

#
# Every line entry has an object of this type in its first column's object
#
class LineEntry:
    def __init__(self, p, devname, line, cols, dest = None):
        self.devname = devname
        
        if (dest == None):
            if (len(self.allowed_dests) > 0):
                dest = _alloc_dest(self.allowed_dests[0])
                pass
            pass
        self.dest = dest
        self.cols = cols
        p.insertLine(line, cols, self)
        if (dest is not None):
            dest.setup(self, p, line, len(cols))
            pass
        return

    def getMountName(self):
        return self.devname

    def newDest(self, p, dest, line, doshutdown=True):
        if (self.dest):
            self.dest.newDest(p, dest, line, doshutdown=doshutdown)
        elif (dest is not None):
            self.dest = dest
            dest.setup(self, p, line, len(self.cols))
            pass
        return

    def clearVal(self, p, line):
        if (self.dest):
            self.dest.clearVal(p, line)
            pass
        return

    def needsWork(self, p, device):
        if (self.dest):
            return self.dest.needsWork(p, device)
        return ()

    def Command(self, p, c):
        return False

    # Called when the user changes the display units.
    def reUnit(self, p, line):
        return

    def __str__(self):
        return self.devname
    
    pass

#
# Dummy entry for the Disk, RAID, and LVM labels
#
class Label(LineEntry):
    allowed_dests = [ ]

    def __init__(self, p, name):
        line = p.numLines()
        LineEntry.__init__(self, p, name, line, (0,))
        p.setColumn(line, 0, name, self)
        return

    pass

class RAIDLabel(Label):
    def __init__(self, p):
        Label.__init__(self, p, "RAIDs")
        return

    def Command(self, p, c):
        if (c == "A"):
            # Find the first unused MD number
            craids = [ ]
            rline = p.findLine(self)
            for l in range(rline + 1, p.numLines()):
                o = p.getObj(l)
                if (o.__class__ != RAID):
                    break
                # Get number from "/dev/mdN"
                craids.append(int(o.devname[7:]))
                pass
            craids.sort()
            i = 0
            for l in craids:
                if (l != i):
                    break
                i += 1
                pass

            RAID(p, "/dev/md%d" % i)
            pass
        else:
            return False
        return True
    
    pass

class LVMLabel(Label):
    def __init__(self, p):
        Label.__init__(self, p, "LVMs")
        return

    def Command(self, p, c):
        if (c == "A"):
            # Get the name from the user
            o = Obj()
            o.p = p
            p.popup = PopupEditVals.PopupEditVals(
                p.getWindow(), 4, 0,
                (("New Volume Group Name", ""),),
                _namelen, self.GetVGNameDone, o)
            pass
        else:
            return False
        return True
    
    def GetVGNameDone(self, o, vals):
        if (vals is None):
            # User aborted
            return
        val = vals[0].strip()
        if (len(val) == 0):
            # Empty string
            return
        p = o.p
        devname = "/dev/" + val
        if (p.findObj(devname) is not None):
            raise ParititionerErr("The name %s is already taken, please use"
                                  + " another name" % val)

        LVMVG(p, devname, 0, 0)
        return

    pass

# Columns for various shared things
_size_col = 3


# Why the strange constraints for logical partitions?
#
# Shamelessly copied and from _log_meta_overlap_constring in
# parted, which shamelessly stole it from
# _partition_get_overlap_constraint (in disk.c)
# This should get rid of the infamous Assertion (metadata_length >
# 0) failed bug for extended msdos disklabels generated by Parted.
# 1) There always is a partition table at the start of ext_part,
#    so we leave a one sector gap there.
# 2)*The partition table of part5 is always at the beginning of
#    the ext_part so there is no need to leave a one sector gap
#    before part5.
#   *There always is a partition table at the beginning of each
#   partition != 5.
# We don't need to worry to much about consistency with
# _partition_get_overlap_constraint because missing it means we
# are in edge cases anyway, and we don't lose anything by just
# refusing to do the job in those cases.
#
# Also, I have discovered that even partition 5 needs an extra
# sector in front of it, at least if it is at the beginning of
# the extended partitions.  Otherwise Linux complains that it
# cannot update partition 5 when told to update it's internal
# partition table.  parted does *not* enforce this rule.

#
# Superclass for the different partition table types.
#
class PartitionTableBase(DestValue):
    # Defaults to be overriden by the individual partitions

    # Can this partition be manipulated by this program?
    usable = False

    # Amount of space required at the beginning of the device for the table.
    reservedBeginning = 0

    # Amount of space required at the end of the device for the table
    reservedEnd = 0

    # Amount of space required before each partition on the device.
    reservedSkip = 0

    # Are extended partitions allowed in this partition table?
    allowExtended = False

    # Maximum number of partitions allowed in this table.
    maxPartitions = 0

    device = None

    def setDevice(self, device):
        self.device = device
        return

    def setup(self, parent, p, line, col):
        self.parent = parent
        if (self.device is None):
            self.device = parent
            pass
        self.column = col
        p.setColumn(line, col, self.name)
        p.setObj(line, col, self)
        if (self.usable):
            self.device.getAlignInfo()
            pass
        return

    def shutdown(self, p):
        InvalidPartitionTable().write(self.parent.devname)

        # Break circular dependencies
        del self.parent
        del self.device
        return

    def Command(self, p, c):
        if (c == "ENTER"):
            o = Obj()
            o.p = p
            p.popup = PopupList.PopupList(p.getWindow(), 4, 0,
                                          _partition_table_types,
                                          self,
                                          self.selectTableDone, o)
            return True
        return False

    def selectTableDone(self, o, val):
        if ((val is None) or (str(val) == str(self))):
            # Aborted or unchanged
            return

        p = o.p
        o.tabletype = val

        if (self.__class__ != InvalidPartitionTable):
            # Make sure the user wants to do this.
            p.popupYesNo("You are changing the partition table.  This will"
                         + " destroy all data on the disk and require a reboot"
                         + " before proceeding.  Do you really want to do"
                         + " this?",
                         self.QueryUserDone, o)
            return

        self.finishTableUpdate(p, val)
        return

    def QueryUserDone(self, o, val):
        if (not val):
            return

        self.finishTableUpdate(o.p, o.tabletype)
        return

    def finishTableUpdate(self, p, tabletype):
        # Write the new table
        tabletype.write(self.parent.devname)
        newtab = tabletype.newInst()

        self.parent.clearPartitions(p, newtab)

        p.redoHighlight()
        return

    def clearVal(self, p, line):
        self.parent.clearPartitions(p, InvalidPartitionTable())
        return

    def __str__(self):
        return self.name

    pass

# This is a dummy one created for extended partitions to provide the
# proper values to the PartitionOwner class.
class ExtendedPartitionTable(PartitionTableBase):
    usable = True
    reservedBeginning = 1

    # Logical partitions require a sector between them, and before
    # the first one, so reservedSkip is 1.  See comments on
    # partitioning and logical partitions.
    reservedSkip = 1

    # The maximum number of partitions is arbitrarily set to 60.
    # That should be enough for anyone.
    maxPartitions = 60

    pass

# The partition table is not valid
class InvalidPartitionTable(PartitionTableBase):
    name = "<inv>"

    def write(self, devname):
        _call_cmd(("dd", "if=/dev/zero", "of=" + devname, "count=100"))

        # Tell the kernel to reread the partition table, thus invalidating it
        _reread_partition_table(devname)
        return

    def newInst(self):
        return InvalidPartitionTable()

    pass

# The partition table is not one of the ones we handle.
class UnknownPartitionTable(PartitionTableBase):
    name = "<???>"

    # Note: You can't choose this or write it, so no need for those methods.
    pass

class MBRPartitionTable(PartitionTableBase):
    name = "MSDOS"
    usable = True
    reservedBeginning = 1
    allowExtended = True
    maxPartitions = 4

    def write(self, devname):
        _call_parted(devname, ["mklabel msdos",])
        return

    def newInst(self):
        return MBRPartitionTable()

    pass

class GUIDPartitionTable(PartitionTableBase):
    name = "GPT"
    usable = True
    reservedBeginning = 34
    reservedEnd = 34
    maxPartitions = 128

    def write(self, devname):
        _call_parted(devname, ["mklabel gpt",])
        return

    def newInst(self):
        return GUIDPartitionTable()

    pass

_partition_table_types = ( InvalidPartitionTable(), MBRPartitionTable(),
                           GUIDPartitionTable() )

_dests = ( FSDest(), RAIDDest(), LVMDest(), SwapDest(), PartitionDest() )

# do_init is set to False when reading in the current status, so the
# partition won't be initialized.
def _alloc_dest(s, do_init=True):
    for d in _dests:
        if (str(d) == s):
            return d.newInst(do_init=do_init)
        pass
    raise PartitionerErr("Invalid destination: " + s)


#
# Superclass for classes that own partitions.
#
class PartitionOwner:
    free_col = 7 # Column where free space is set
    subpartitions = None
    devname = None
    partitionOffset = 0

    # Separater between the device name and partition number
    namenumsep = ""

    def __init__(self, p, pdevname, line, numsects, sectsize, table):
        self.table = table
        self.numsects = numsects
        self.freesects = numsects - table.reservedBeginning - table.reservedEnd
        self.sectsize = sectsize
        self.partitions = []
        self.partitiondevname = pdevname

        p.setSizeColumn(self, line, _size_col, numsects)

        p.setColumn(line, self.free_col - 1, "Free:")
        p.setSizeColumn(self, line, self.free_col, self.freesects)
        return

    def reUnit(self, p, line):
        p.setSizeColumn(self, line, _size_col, self.numsects)
        p.setSizeColumn(self, line, self.free_col, self.freesects)
        return

    # Called when free space is added or removed
    def addToFreeSpace(self, p, line, size):
        self.freesects += size
        p.setSizeColumn(self, line, self.free_col, self.freesects)
        return

    # Get the minimum and optimal partition alignments
    def getAlignInfo(self):
        # Get minimum and optimal alignment for a disk.
        out = _call_cmd(("blockdev", "--getiomin", self.devname))
        self.minalign = int(out.strip()) / 512
        if (self.minalign == 0):
            self.minalign = 1
            pass
        out = _call_cmd(("blockdev", "--getioopt", self.devname))
        self.optalign = int(out.strip()) / 512
        if (self.optalign == 0):
            # Align to Windows standards, 1MiB
            self.optalign = 1024000 / self.sectsize
            pass
        if (self.optalign < self.minalign):
            self.optalign = self.minalign
            pass
        return

    def _doAlignV(self, v, alignv, round_up):
        mod = v % alignv
        if (mod != 0):
            if (round_up):
                v += alignv - mod
            else:
                v -= mod
                pass
            pass
        return v

    def alignValue(self, p, v, round_up=True):
        if (p.align_opt):
            v = self._doAlignV(v, self.optalign, round_up)
        else:
            v = self._doAlignV(v, self.minalign, round_up)
            pass
        return v

    def partitionAdded(self, p, part, line=None):
        if (line is None):
            line = p.findLine(self)
            pass
        self.partitions.append(part)

        # Recalculate free space
        self.freesects -= part.numsects
        p.setSizeColumn(self, line, self.free_col, self.freesects)
        return

    # Get a list of free areas on the disk.  Note that it returns one more
    # than the end.
    def getUsedAreas(self):
        used = [ ]
        for i in self.partitions:
            used.append( (i.sectstart, i.sectstart + i.numsects) )
            pass
        if (self.table.reservedEnd != 0):
            used.append( (self.numsects - self.table.reservedEnd,
                          self.numsects) )
            pass
        used.sort()
        return used

    def printInfo(self, p, extra=None):
        s = (("More information for %s:\n"
             + "Sector size:       %d")
             % (self.devname, self.sectsize))
        if (self.table and self.table.usable):
            s += (("\nMinimum alignment: %d\n"
                  + "Optimum alignment: %d")
                  % (self.minalign, self.optalign))
            pass
        uuid = _get_dev_uuid(self.devname)
        if (uuid):
            s += "\nUUID: " + uuid
            pass
        if (extra):
            s += extra
            pass
        p.popupWin(s)
        return True

    def addCmd(self, p, extended):
        if (len(self.partitions) >= self.table.maxPartitions):
            raise PartitionerErr("Disk already has %d partitions"
                                 % self.table.maxPartitions)

        # Get a sorted list of used space so we can search
        used = self.getUsedAreas()

        # Find the first hole and use it for the initial size
        csect = (self.partitionOffset + self.table.reservedBeginning
                 + self.table.reservedSkip)
        found = False
        for i in used:
            csect = self.alignValue(p, csect)
            # Make sure we can fit an entire alignment section in, thus the
            # second alignValue below.
            if (i[0] > self.alignValue(p, csect+1)):
                found = True
                sectstart = csect
                numsects = i[0] - csect
                break
            csect = i[1] + self.table.reservedSkip
            pass
        if (not found):
            csect = self.alignValue(p, csect)
            if ((csect - self.partitionOffset) >= self.numsects):
                raise PartitionerErr("No more space on disk")
            sectstart = csect
            numsects = self.numsects - (csect - self.partitionOffset)
            pass
        numsects = self.alignValue(p, numsects, round_up=False)

        o = Obj()
        o.extended = extended
        o.p = p
        o.sectstart = sectstart
        o.numsects = numsects
        
        o.first = True # Don't redraw the sc widget the first time

        self.continueEdit(o) 
        return

    def continueEdit(self, o):
        p = o.p
        if (not o.first):
            p.redraw()
            pass
        
        p.popup = PopupEditVals.PopupEditVals(
            p.getWindow(), 4, 0,
            (("Start Sector", p.units.convToStr(self, o.sectstart)),
             ("Num Sectors", p.units.convToStr(self, o.numsects))),
            _sizelen, self.addEditDone, o)
        return
    
    def partitionUpdatedHook(self):
        # Hook for special ties when a partitions is created.
        return

    def addEditDone(self, o, vals):
        if (vals is None):
            # User aborted
            return
        
        o.first = False # Redraw the sc widget on errors
        
        p = o.p
        try:
            sectstart = p.units.convFromStr(p, self, vals[0])
        except:
            p.popupWin("Starting sector '" + vals[0]
                       + "' was not a valid number, please"
                       + " try again",
                       self.continueEdit, o)
            return
        
        try:
            numsects = p.units.convFromStr(p, self, vals[1])
        except:
            p.popupWin("size '" + vals[1].strip()
                       + "' was not a valid number, please"
                       + " try again",
                       self.continueEdit, o)
            return

        err = self.checkAreaFree(sectstart, numsects)
        if (err is not None):
            # Keep the user's entered values
            o.sectstart = sectstart
            o.numsects = numsects
            p.popupWin(("Given area %d %d %s, please try again")
                       % (sectstart, numsects, err),
                       self.continueEdit, o)
            return

        o.sectstart = self.alignValue(p, sectstart)
        o.numsects = self.alignValue(p, numsects)

        if (sectstart != o.sectstart or numsects != o.numsects):
            p.popupWin(("sector values (%d, %d) were not aligned properly,"
                       + " trying again with properly aligned values"
                       + " displayed.")
                       % (sectstart, numsects),
                       self.continueEdit, o)
            return

        del sectstart
        del numsects

        extended = o.extended

        # Add the partition
        line = p.findLine(self)
        kernel_update_worked = True
        if (extended):
            try:
                _call_parted(self.partitiondevname,
                             ["mkpart extended %d %d"
                              % (o.sectstart,
                                 o.sectstart + o.numsects - 1),])
            except CmdErr as e:
                if ("Error informing the kernel" in e.out):
                    kernel_update_worked = False
                else:
                    raise
                pass
        else:
            try:
                _call_parted(self.partitiondevname,
                             ["mkpart %s %d %d"
                              % (self.subpartitions,
                                 o.sectstart,
                                 o.sectstart + o.numsects - 1),])
            except CmdErr as e:
                if ("Error informing the kernel" in e.out):
                    kernel_update_worked = False
                else:
                    raise
                pass
            pass

        self.partitionUpdatedHook()

        # We used to have parted return the added partition id (which
        # itself required a hack, since for some odd reason parted
        # didn't do this by default) and use that to add the proper
        # information.  This had problems, though, since adding a
        # partition with existing content could cause udev to
        # instantiate RAIDs and LVMs, and the partitioner wouldn't
        # know about this.  So to work around both of these problem,
        # just reread everything when a partition is added.  It's
        # slow, but it's reliable.
        p.reRead()

        if (not kernel_update_worked):
            p.popupWin("Error informing the kernel about the partitions"
                       + " update for " + name + ", so linux will not"
                       + " know about the changes.  You will need to"
                       + " restart the system to pick up the partition"
                       + " changes.")
            pass
        return

    def delCmd(self, p, part):
        i = 0
        for x in self.partitions:
            if (x == part):
                break
            i += 1
            pass

        # Remove it from whatever it belongs to.
        if (part.dest):
            part.dest.shutdown(p)
            pass

        line = p.lineOf(0, self)

        _call_parted(self.partitiondevname, ["rm %d" % part.num,])

        del self.partitions[i]
        p.deleteLine(line + i + 1)
        
        self.partitionUpdatedHook()

        # Recalculate free space
        self.freesects += part.numsects
        p.setSizeColumn(self, line, self.free_col, self.freesects)

        # Break circular references
        del part.parent
        dest = part.dest
        del part.dest
        if (dest is not None):
            del dest.subtype
            del dest.value
        return

    # Called when a partition destination is changed to something else
    def partitionShutdown(self):
        return

    # Check if the given area on the disk is free
    def checkAreaFree(self, sectstart, numsects):
        if (sectstart < self.partitionOffset):
            return "starts before beginning"
        if (sectstart < self.partitionOffset + self.table.reservedBeginning
            + self.table.reservedSkip):
            return "starts in reserved area at the partition beginning"
        if (numsects < 2):
            return "minimum size is 2 sectors"
        end = sectstart + numsects - 1
        if (end > (self.partitionOffset + self.numsects)):
            return "goes past end"
        
        used = self.getUsedAreas()
        for u in used:
            if (end < u[0]):
                continue
            if (sectstart >= u[1]):
                if (sectstart < u[1] + self.table.reservedSkip):
                    return ("This partition requires a reserved area before"
                            + " the beginning")
                continue
            return "is inside another used area"
        
        return None
    
    pass

#
# Entire disks
#
class Disk(LineEntry, PartitionOwner):
    # Note that you can add more destinations here, but really, disks should
    # only be partitioned.  Too many things don't work otherwise.
    allowed_dests = [ "part" ]
    coloffset = 1
    subpartitions = "primary"

    # Place to put the free space as a partition destination
    free_col = 8

    def __init__(self, p, devname, numsects, sectsize, table):
        self.numsects = numsects
        self.sectsize = sectsize
        self.running = True
        if (table is not None):
            dest = PartitionDest(table=table, do_init=False)
        else:
            self.table = None
            pass

        line = p.lineOf(0, p.raidObj)

        LineEntry.__init__(self, p, devname, line,
                           (-1, _namelen - 1, -_sizelen, -_sizelen, -1),
                           dest)

        p.disks.append(devname)
        p.setColumn(line, 1, devname)
        return

    # Called when we are set with a partition destination
    def partitionInit(self, p, line, table):
        PartitionOwner.__init__(self, p, self.devname, line, self.numsects,
                                self.sectsize, table)
        return

    # Called when a partition destination is changed to something else
    def partitionShutdown(self):
        self.table = None
        return

    def reUnit(self, p, line):
        p.setSizeColumn(self, line, 3, self.numsects)
        if (self.table is None):
            return

        PartitionOwner.reUnit(self, p, line)
        return

    def clearPartitions(self, p, table):
        if (self.table is None):
            return

        line = p.lineOf(0, self) + 1
        for i in self.partitions:
            p.deleteLine(line)
            pass

        self.table = table
        if (table is None):
            self.freesects = self.numsects
            del self.partitions
        else:
            table.setup(self, p, p.lineOf(0, self), 6)

            self.freesects = (self.numsects - self.table.reservedBeginning
                              - self.table.reservedEnd)
            self.partitions = []
            line -= 1
            p.setSizeColumn(self, line, self.free_col, self.freesects)
            pass
        return

    def set(self, str):
        # FIXME - do anything?
        return

    def Command(self, p, c):
        if (c == "A"):
            if (not self.table.usable):
                raise PartitionerErr("The partition table on this disk is not"
                                     + " usable for partitioning")
            self.addCmd(p, False)
        elif (c == "E"):
            if (not self.table.usable):
                raise PartitionerErr("The partition table on this disk is not"
                                     + " usable for partitioning")
            if (not self.table.allowExtended):
                return False
            self.addCmd(p, True)
        elif (c == "I"):
            identity = None
            try:
                if (os.path.exists("/lib/udev/scsi_id")):
                    scsi_id = "/lib/udev/scsi_id"
                elif (os.path.exists("/lib64/udev/scsi_id")):
                    scsi_id = "/lib64/udev/scsi_id"
                elif (os.path.exists("/lib32/udev/scsi_id")):
                    scsi_id = "/lib32/udev/scsi_id"
                else:
                    identity = "\nUnable to find scsi_id cmd, so no info"
                    raise CmdErr(None, None, None, None)

                out = _call_cmd((scsi_id, "--whitelisted",
                                 "--export", self.devname))
                out = out.split("\n")
                identity = ""
                for i in out:
                    i = i.split('=', 1)
                    if (len(i) != 2):
                        continue
                    if (i[0] == "ID_VENDOR"):
                        identity += "\nVendor:            " + i[1]
                    elif (i[0] == "ID_MODEL"):
                        identity += "\nModel:             " + i[1]
                    elif (i[0] == "ID_SERIAL"):
                        identity += "\nSerial:            " + i[1]
                        pass
                    pass
                pass
            except CmdErr as e:
                pass
            return self.printInfo(p, extra=identity)
        else:
            return False
        return True
    
    pass

#
# A partition on a disk or in an extended partition
#
class Partition(LineEntry):
    allowed_dests = [ "fs", "RAID", "LVM", "swap" ]

    # Parent is the disk that owns this partition
    def __init__(self, p, parent, devname, num, line,
                 sectstart, numsects, dest = None,
                 boot=False):
        self.num = num
        coloff = parent.coloffset + 1
        namesize = _namelen - coloff
        LineEntry.__init__(self, p, devname, line,
                           (-coloff, namesize, -_sizelen, -_sizelen, -1),
                           dest)
        self.parent = parent
        self.sectstart = sectstart
        self.numsects = numsects
        self.boot = boot
        
        if (self.boot):
            p.setColumn(line, 0, "b")
            pass
        p.setColumn(line, 1, devname)
        p.setSizeColumn(parent, line, 2, sectstart)
        p.setSizeColumn(parent, line, 3, numsects)
        self.parent.partitionAdded(p, self)
        return

    def reName(self, p, line, num):
        self.num = num
        self.devname = (self.parent.partitiondevname
                        + self.parent.namenumsep + str(num))
        p.setColumn(line, 1, self.devname)
        return

    def reUnit(self, p, line):
        p.setSizeColumn(self.parent, line, 2, self.sectstart)
        p.setSizeColumn(self.parent, line, 3, self.numsects)
        return

    def set(self, str):
        _call_parted(self.parent.partitiondevname,
                     ["set %d %s" % (self.num, str),])
        return

    def Command(self, p, c):
        if (c == "D"):
            p.popupYesNo("Do you really want to delete the partition %s?"
                         % self.devname,
                         self.delQueryDone, p)
        elif (c == "B"):
            # Toggle the boot flag
            line = p.lineOf(0, self)
            if (self.boot):
                _call_parted(self.parent.partitiondevname,
                             ["set", str(self.num), "boot", "off"])
                self.boot = False
                p.setColumn(line, 0, "")
            else:
                _call_parted(self.parent.partitiondevname,
                             ["set", str(self.num), "boot", "on"])
                self.boot = True
                p.setColumn(line, 0, "b")
                pass
            pass
        else:
            return LineEntry.Command(self, p, c)
        return True

    def delQueryDone(self, p, val):
        if (not val):
            return

        self.parent.delCmd(p, self)
        return

    pass

#
# Extended partition
#
class ExtendedPartition(LineEntry, PartitionOwner):
    allowed_dests = [ ]
    coloffset = 2
    subpartitions = "logical"

    def __init__(self, p, parent, devname, num, line, sectstart, numsects):
        # Only one extended partition per disk, please
        for part in parent.partitions:
            if (part.__class__ == ExtendedPartition):
                raise PartitionerErr("Disk already has an extended partition")
            pass

        self.namenumsep = parent.namenumsep
        self.num = num
        self.minalign = parent.minalign
        self.optalign = parent.optalign
        self.sectsize = parent.sectsize
        coloff = parent.coloffset + 1
        namesize = _namelen - coloff
        LineEntry.__init__(self, p, devname, line,
                           (-coloff, namesize, -_sizelen, -_sizelen,
                            -1, -_typelen, -6, -_sizelen),
                           None)
        p.setColumn(line, 5, "ext")
        self.parent = parent
        self.sectstart = sectstart
        self.numsects = numsects
        self.partitionOffset = sectstart

        PartitionOwner.__init__(self, p, parent.devname,
                                line, numsects, parent.sectsize,
                                ExtendedPartitionTable())
        self.parent.partitionAdded(p, self)
        p.setColumn(line, 1, devname)
        p.setSizeColumn(self, line, 2, sectstart)
        return

    def reUnit(self, p, line):
        PartitionOwner.reUnit(self, p, line)
        p.setSizeColumn(self, line, 2, self.sectstart)
        return

    def partitionUpdatedHook(self):
        self.parent.partitionUpdatedHook()
        return

    def Command(self, p, c):
        if (c == "A"):
            self.addCmd(p, False)
        elif (c == "D"):
            p.popupYesNo(("Do you really want to delete the extended partition"
                          + " %s?  This will cause all logical partitions on"
                          + " this device to be deleted, too.")
                         % self.devname,
                         self.delQueryDone, p)
        else:
            return False
        return True

    def delCmd(self, p, part):
        # Sigh.  When you delete an logical partition, all logical
        # partitions numerically after it get renumbered.
        num = part.num
        PartitionOwner.delCmd(self, p, part)
        for i in self.partitions:
            if (i.num < num):
                continue

            i.reName(p, p.lineOf(0, i), i.num-1)
            pass
        return

    def delQueryDone(self, p, val):
        if (not val):
            return

        while self.partitions:
            self.delCmd(p, self.partitions[0])
            pass
        self.parent.delCmd(p, self)
        return

    pass

class RAIDLevel:
    _raid_types = ("inactive", "raid1", "multipath")

    def __init__(self, level="raid1"):
        self.level = level
        return

    def __str__(self):
        return self.level

    def setup(self, parent, p, line, col):
        self.parent = parent
        self.col = col
        p.setObj(line, col, self)
        p.setColumn(line, col, self.level)
        return

    def Command(self, p, c):
        if (c == "ENTER"):
            llist = self._raid_types
            if (self.parent.vols and self.parent.running):
                llist = ("inactive", self.level)
                pass
            o = Obj()
            o.p = p
            p.popup = PopupList.PopupList(p.getWindow(), 4, 0,
                                          llist, self.level,
                                          self.selectRAIDLevelDone, o)
            return True
        return False

    def selectRAIDLevelDone(self, o, val):
        p = o.p
        if ((val is None) or (val == self.level)):
            # Unchanged
            return

        if (self.level == "inactive"):
            # Going active
            self.parent.activate(p, val)
        elif (val == "inactive"):
            # Going inactive
            self.parent.deactivate(p)
            pass
            
        self.level = val
        p.setColumn(p.linepos, self.col, self.level)
        p.redoHighlight()
        return

    pass

# A RAID, it consists of one or more partitions
class RAID(LineEntry, PartitionOwner):
    allowed_dests = [ "fs", "LVM", "swap", "part", "RAID" ]
    coloffset = 1
    subpartitions = "primary"

    # Place to put the free space when a partition destination
    free_col = 8

    namenumsep = "p"

    def __init__(self, p, devname, numsects=0, sectsize=512, tabletype=None,
                 dest=None, level="raid1"):
        self.numsects = numsects
        self.sectsize = sectsize
        self.running = False

        self.vols = []
        self.size = 0

        # Add it right before the "LVM" line
        line = p.lineOf(0, p.lvmObj)

        # If it has a partition table, set it up properly for that
        if (tabletype is not None):
            dest = PartitionDest(table=tabletype, do_init=False)
        else:
            self.table = None
            pass

        LineEntry.__init__(self, p, devname, line,
                           (-1, _namelen - 1, _sizelen, -_sizelen, -1),
                           dest)

        self.level = RAIDLevel(level=level)

        if (tabletype is not None):
            self.partitionInit(p, line, tabletype)
            pass

        p.setColumn(line, 1, devname)
        p.setSizeColumn(self, line, 3, numsects)
        p.raids.append(devname)

        self.level.setup(self, p, line, 2)
        return

    # Called when we are set with a partition destination
    def partitionInit(self, p, line, table):
        PartitionOwner.__init__(self, p, self.devname, line, self.numsects,
                                self.sectsize, table)
        return

    # Called when a partition destination is changed to something else
    def partitionShutdown(self):
        self.table = None
        return

    def getMountName(self):
        # For RAID, prefer the UUID as device names might get
        # renumbered but the UUIDs don't change arbitrarily.  Note
        # that regenerating a filesystem does change the UUID, so
        # we cannot pre-fetch the UUID since the filesystem may be
        # regenerated at quit time.
        uuid = _get_dev_uuid(self.devname)
        if (uuid):
            mp = "UUID=" + uuid
        else:
            mp = self.devname
            pass
        return mp

    def reUnit(self, p, line):
        p.setSizeColumn(self, line, 3, self.numsects)
        if (self.table is None):
            return

        PartitionOwner.reUnit(self, p, line)
        return

    def partitionUpdatedHook(self):
        # For some reason parted doesn't tell the kernel about new partitions
        # on md devices.  So force it.
        _reread_partition_table(self.devname)
        return

    def clearPartitions(self, p, table):
        if (self.table is None):
            return

        line = p.lineOf(0, self) + 1
        for i in self.partitions:
            p.deleteLine(line)
            pass

        self.table = table

        if (table is None):
            self.freesects = self.numsects
            del self.partitions
        else:
            table.setup(self, p, p.lineOf(0, self), 6)

            self.freesects = (self.numsects - self.table.reservedBeginning
                              - self.table.reservedEnd)
            self.partitions = []
            line -= 1
            p.setSizeColumn(self, line, self.free_col, self.freesects)
            pass
        return

    def set(self, str):
        # FIXME - do anything?
        return

    def setNumSects(self, p, line, numsects, sectsize):
        diff = numsects - self.numsects
        if (diff == 0):
            return
        self.numsects = numsects
        self.sectsize = sectsize # FIXME = sector size change?
        p.setSizeColumn(self, line, 3, numsects)
        if (self.table):
            self.addToFreeSpace(p, line, diff)
            pass
        return

    def querySize(self, p):
        line = p.findLine(self)
        (numsects, sectsize, err) = _disk_info_from_fdisk(self.devname)
        if (numsects is None):
            # It doesn't exist, just set it to zero
            numsects = 0
            sectsize = 512
            pass
        self.setNumSects(p, line, numsects, sectsize)
        return

    def deactivate(self, p):
        if (not self.running):
            return
        _call_mdadm(self.devname, "--stop")
        self.setNumSects(p, p.findLine(self), 0, 512)
        self.running = False
        return

    def activate(self, p, level):
        if (self.running):
            return

        if (level == "raid1"):
            if (len(self.vols) == 0):
                return
            level = "1"
            pass
        elif (level == "multipath"):
            if (len(self.vols) < 2):
                raise PartitionerErr("Unable to activate a multipath array"
                                     + " with less than 2 devices")
            ser = []
            for v in self.vols:
                if (v.__class__ != Partition):
                    raise PartitionerErr("All members of a multipath RAID"
                                         + " must be partitions.  Activation"
                                         + " cancelled.")
                # Get all the UUIDs for the devices and make sure they
                # match.
                try:
                    out = _call_cmd(("/lib/udev/scsi_id", "--whitelisted",
                                     "--export", v.parent.partitiondevname))
                    out = out.split("\n")
                    cid = ""
                    for i in out:
                        i = i.split('=', 1)
                        if (len(i) != 2):
                            continue
                        if (i[0] == "ID_SERIAL"):
                            cid = i[1]
                            break
                        pass
                    ser.append(cid)
                    pass
                except CmdErr as e:
                    ser.append("")
                    pass
                pass
            firstid = ser[0]
            for cid in ser[1:]:
                if (firstid != cid):
                    raise PartitionerErr("All members of a multipath RAID"
                                         + " be the same disk.  The disks"
                                         + " do not have matching serial"
                                         + " numbers.  Activate aborted.")
                pass
            firstnum = self.vols[0].num
            for v in self.vols[1:]:
                if (v.num != firstnum):
                    raise PartitionerErr("All members of a multipath RAID"
                                         + " be the same partition number."
                                         + " The partitions in the RAID do not"
                                         + " have matching numbers.  Activate"
                                         + " aborted.")
                pass
            pass
        else:
            raise PartitionerErr("Unknown level: %s" % level)
        
        opts = ["--force", "--level=%s" % level,
                "--metadata=0.90", "--run",
                "--raid-devices=%d" % len(self.vols)]
        for vol in self.vols:
            opts.append(vol.devname)
            pass
        _call_mdadm(self.devname, "--create", opts)
        self.running = True
        self.querySize(p)
        return

    def addVol(self, p, vol):
        nvols = len(self.vols)
        if (str(self.level) == "inactive"):
            # Don't do anything for inactive arrays.
            pass
        elif (str(self.level) == "multipath"):
            raise PartitionerErr("Cannot resize active multpath arrays, you"
                                 + " must deactivate them first.")
        elif (nvols == 0):
            # Initial creation
            # grub does not support anything but 0.90 RAID metadata
            # so we are stuck with that.
            #  "--metadata=1.2",
            level = self.level
            if level == "raid1":
                level = "1"
            _call_mdadm(self.devname, "--create",
                        ["--force", "--level=%s" % str(level),
                         "--metadata=0.90", "--run", "--raid-devices=1",
                         vol.devname])
            self.running = True
        else:
            _call_mdadm(self.devname, "--grow",
                        ["--force", "--raid-devices=%d" % (nvols + 1)])
            _call_mdadm(self.devname, "--add", [vol.devname,])
            pass
            
        self.vols.append(vol)
        self.querySize(p)
        return

    def addVolInit(self, vol):
        self.running = True
        self.vols.append(vol)
        return

    def removeVol(self, p, vol):
        if (str(self.level) == "multipath"):
            raise PartitionerErr("Cannot resize active multipath arrays, you"
                                 + " must deactivate them first.")
        if (self.running):
            nvols = len(self.vols)
            r = self.devname
            if (nvols == 1):
                # We are going to no volumes, stop the raid
                _call_mdadm(r, "--stop")
                self.setNumSects(p, p.findLine(self), 0, 512)
                self.running = False
                # Make sure the disk doesn't come back
                _call_cmd(["mdadm", "--zero-superblock", vol.devname])
            else:
                _call_mdadm(r, "--fail", [vol.devname,])
                _call_mdadm(r, "--remove", [vol.devname,])
                _call_mdadm(r, "--grow",
                            ["--force", "--raid-devices=%d" % (nvols - 1)])
                if (str(self.level) != "multipath"):
                    # Make sure the disk doesn't come back
                    _call_cmd(["mdadm", "--zero-superblock", vol.devname])
                    pass
                self.querySize(p)
                pass
            pass
        
        del self.vols[self.vols.index(vol)]
        return

    def Command(self, p, c):
        if (c == "A"):
            if (self.table is None):
                return False
            if (not self.table.usable):
                raise PartitionerErr("The partition table on this device is not"
                                     + " usable for partitioning")
            self.addCmd(p, False)
        elif (c == "E"):
            if (self.table is None):
                return False
            if (not self.table.usable):
                raise PartitionerErr("The partition table on this device is not"
                                     + " usable for partitioning")
            if (not self.table.allowExtended):
                return False
            self.addCmd(p, True)
        elif (c == "D"):
            p.popupYesNo("Do you really want to delete raid %s?"
                         % self.devname,
                         self.delQueryDone, p)
        elif (c == "I"):
            return self.printInfo(p)
        else:
            return False
        return True

    def delQueryDone(self, p, val):
        if (not val):
            return

        if (self.running):
            _call_mdadm(self.devname, "--stop")
            self.running = False
            pass
        self.clearPartitions(p, None)
        while (len(self.vols) > 0):
            o = self.vols[0]
            l = p.findLine(o)
            o.clearVal(p, l)
            pass
        del p.raids[p.raids.index(self.devname)]
        p.deleteLine(p.findLine(self))
        return

    pass

# An LVM volume group consists of one or more partitions or RAIDs
class LVMVG(LineEntry):
    allowed_dests = [ ]

    def __init__(self, p, devname, numsects, freesects):
        # Add it at the end
        line = p.numLines()

        # VGs are not really created until they have a PV, so keep track of
        # that.
        self.really_exists = False

        LineEntry.__init__(self, p, devname, line,
                           (-1, _namelen - 1, -_sizelen, -_sizelen,
                            -1, -_typelen, -6, -_sizelen),
                           None)
        p.setColumn(line, 1, devname)
        p.setColumn(line, 6, "Free:")
        self.sectsize = 512 # FIXME - is this right?
        self.setSize(p, line, numsects, freesects)
        self.pvols = []
        self.lvols = []
        p.vgs.append(devname)
        return

    def reUnit(self, p, line):
        p.setSizeColumn(self, line, _size_col, self.numsects)
        p.setSizeColumn(self, line, 7, self.freesects)
        return

    def setSize(self, p, line, numsects, freesects):
        self.numsects = numsects
        self.freesects = freesects
        self.reUnit(p, line)
        return

    def recalcSize(self, p, line):
        if (self.really_exists):
            out = _call_lvmdispcmd("vgs", [self.devname,])
            w = out.split()
            self.setSize(p, line, int(w[5]), int(w[6]))
        else:
            self.setSize(p, line, 0, 0)
            pass
        return

    def addPVolInit(self, pvol):
        self.pvols.append(pvol)
        self.really_exists = True
        return

    def addPVol(self, pvol, p):
        npvols = len(self.pvols)
        r = self.devname
        if (npvols == 0):
            # Initial creation
            _call_lvmcmd("vgcreate", [r, pvol.devname])
            self.really_exists = True
        else:
            _call_lvmcmd("vgextend", [r, pvol.devname])
            pass
            
        self.addPVolInit(pvol)
        self.recalcSize(p, p.findLine(self))
        return

    def removePVol(self, pvol, p):
        npvols = len(self.pvols)
        r = self.devname
        if (npvols == 1):
            # We are going to no physical volumes, remove the vg
            _call_lvmcmd("vgremove", ["-f", r])
            self.really_exists = False
            for v in self.lvols:
                p.deleteLine(p.findLine(v))
                pass
            self.lvols = []
        else:
            _call_lvmcmd("vgreduce", [r, pvol.devname])
            pass
        
        del self.pvols[self.pvols.index(pvol)]
        self.recalcSize(p, p.findLine(self))
        return

    def addLVolInit(self, lvol):
        self.lvols.append(lvol)
        return

    def addLVol(self, p, lvol):
        self.lvols.append(lvol)
        self.recalcSize(p, p.findLine(self))
        return

    def removeLVol(self, lvol, p):
        _call_lvmcmd("lvremove", ["-f", lvol.devname])
        del self.lvols[self.lvols.index(lvol)]
        p.deleteLine(p.findLine(lvol))
        self.recalcSize(p, p.findLine(self))
        return

    def Command(self, p, c):
        if (c == "A"):
            o = Obj()
            o.p = p
            o.name = ""
            o.numsects = self.freesects
            self.continueEdit(o)
        elif (c == "D"):
            p.popupYesNo(("Do you really want to delete the volume group %s?"
                          + "  All logical volumes in this group will be"
                          + " deleted, too.")
                         % self.devname,
                         self.delQueryDone, p)
        else:
            return False
        return True

    def delQueryDone(self, p, val):
        if (not val):
            return

        while self.lvols:
            self.removeLVol(self.lvols[0], p)
            pass
        while self.pvols:
            v = self.pvols[0]
            v.clearVal(p, p.findLine(v))
            pass
        p.deleteLine(p.findLine(self))
        del p.vgs[p.vgs.index(self.devname)]
        return

    def continueEdit(self, o):
        p = o.p
        p.popup = PopupEditVals.PopupEditVals(
            p.getWindow(), 4, 0,
            (("New Logical Volume Name", o.name),
             ("Logical Volume Size",
              p.units.convToStr(self, o.numsects))),
            _namelen, self.GetLVNameDone, o)
        return

    def alignValue(self, p, v, round_up=True):
        # No need for alignment, let the LVM tools do that
        return v

    def GetLVNameDone(self, o, vals):
        if (vals is None):
            # User aborted
            return
        name = vals[0].strip()
        if (len(name) == 0):
            # Empty string
            return
        p = o.p
        devname = self.devname + "/" + name
        if (p.findObj(devname) is not None):
            o.name = name
            p.popupWin(("The name %s is already taken, please use"
                       + " another name") % devname,
                       self.continueEdit, o)
            return

        try:
            numsects = p.units.convFromStr(p, self, vals[1])
        except Exception as e:
            p.popupWin("Size '" + vals[1].strip()
                       + "' was not a valid number, please"
                       + " try again",
                       self.continueEdit, o)
            return

        _call_lvmcmd("lvcreate", ["--name", name,
                                  "--size", str(numsects) + "s",
                                  self.devname])

        # Calculate the actual number of sectors
        out = _call_lvmdispcmd("lvs", [devname,])
        w = out.split()
        numsects = int(w[3])

        lvol = LVMLV(p, devname, self, numsects)
        self.addLVol(p, lvol)
        return

    pass

# An LVM logical volume is part of a volume group
class LVMLV(LineEntry):
    allowed_dests = [ "fs", "swap" ]

    def __init__(self, p, devname, vg, numsects, dest = None):
        self.lvname = devname[devname.rindex("/") + 1:]
        # Add it at the end of the VG's entries
        line = p.findLine(vg) + 1
        while (line < p.numLines()):
            o = p.getObj(line)
            if (o.__class__ != LVMLV):
                break
            line += 1
            pass
        self.vg = vg
        self.numsects = numsects
        LineEntry.__init__(self, p, devname, line,
                           (-2, _namelen - 2, -_sizelen, -_sizelen, -1),
                           dest)
        p.setColumn(line, 1, self.lvname)
        p.setSizeColumn(vg, line, _size_col, numsects)
        return

    def getMountName(self):
        # For LVM devices, use /dev/mapper/<vg>-<lv>
        vgname = self.vg.devname[5:]
        return "/dev/mapper/" + vgname + "-" + self.lvname

    def reUnit(self, p, line):
        p.setSizeColumn(self.vg, line, _size_col, self.numsects)
        return

    def set(self, str):
        # FIXME - do anything?
        return

    def Command(self, p, c):
        if (c == "D"):
            p.popupYesNo("Do you really want to delete the logical volume %s?"
                         % self.devname,
                         self.delQueryDone, p)
        else:
            return False
        return True

    def delQueryDone(self, p, val):
        if (not val):
            return

        self.vg.removeLVol(self, p)
        return

    pass

def _disk_info_from_fdisk(d):
    # Grrr.  Parted doesn't print any disk information if the
    # label isn't valid.  Use fdisk to get it.
    prog = subprocess.Popen(("fdisk", d),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, close_fds=True)
    (out, err) = prog.communicate(bytes("\np\nq\n".encode("utf8")))
    out = out.decode("utf8")
    err = err.decode("utf8")
    if (prog.returncode != 0):
        return (None, None, err)
    l = out.split("\n")
    for i in range(0, len(l)):
        if (l[i].startswith("Disk /dev/")):
            # Found the one we want
            w = l[i].split()
            diskbytesize = int(w[4])
            w = l[i+2].split()
            sectsize = int(w[5])
            numsects = diskbytesize / sectsize
            return (numsects, sectsize, None)
        pass
    return (None, None, "Unable to find disk info for %s" % d)

def _disk_info(d):
    try:
        o = _call_parted(d, ["print",])
    except CmdErr as e:
        (numsects, sectsize, err) = _disk_info_from_fdisk(d)
        if (err):
            return (0, 0, None, None, err)
        return (numsects, sectsize, None, None, None)
    j = json.loads(o)["disk"]
    sectsize = int(j["logical-sector-size"])
    numsects = int(j["size"].rstrip("s"))
    if (j["label"] == "msdos"):
        tabletype = MBRPartitionTable()
    elif (j["label"] == "gpt"):
        tabletype = GUIDPartitionTable()
    elif (j["label"] == "loop"):
        # It appears, at least on MD devices, that a device without
        # a partition table directly used for a filesytem appears
        # as "loop".  Don't process partitions.
        tabletype = None
        lines = None
    else:
        tabletype = UnknownPartitionTable()
        # Don't attempt to process the partitions.
        lines = None
        pass
    return (numsects, sectsize, tabletype, j["partitions"], None)

def _process_dev_by_contents(name):
    i = _get_file_info(name)
    fstype = None
    for d in _fs_types[1:]:
        if d.match(i):
            fstype = d.newInst()
            break
        pass
    if (fstype is None) and ("swap file" in i):
        dest = _alloc_dest("swap", do_init=False)
    else:
        dest = FSDest(subtype=fstype, do_init=False)
        pass
    return dest

def _process_dev_by_fstab(name, fstab_info, realdevname=None):
    if (realdevname is None):
        realdevname = name
        pass
    dest = None
    uuid = _get_dev_uuid(name)

    if (realdevname in fstab_info):
        key = realdevname
    elif (uuid and ("UUID=" + uuid) in fstab_info):
        key = "UUID=" + uuid
    else:
        key = None
        pass
    if (key):
        # Pull info from the fstab for the mount point.
        i = fstab_info[key]
        del fstab_info[key]
        dest = FSDest(subtype=i[1], value=MountPoint(value=i[0]),
                      do_init=False, options=i[2], dump=i[3],
                      passnum=i[4])
        pass
    return dest

def _process_partitions(p, device, partitions, devname, split, tabletype,
                        fstab_info):
    line = p.lineOf(0, device) + 1
    extended = None
    for part in partitions:
        name = devname + split + str(part["number"])
        num = int(part["number"])
        sectstart = int(part["start"].rstrip("s"))
        numsects = int(part["end"].rstrip("s")) - sectstart + 1
        if (part["type"] == "extended"):
            extended = ExtendedPartition(p, device, name, num,
                                         line, sectstart, numsects)
            line += 1
            pass
        else:
            if (num <= 4 or not tabletype.allowExtended):
                # Primary partition
                parent = device
            else:
                # Logical partition
                parent = extended
                pass

            boot = False
            dest = _process_dev_by_fstab(name, fstab_info)
            if (dest):
                pass
            elif "type-id" in part and part["type-id"] == "0x82":
                # A swap partition
                dest = _alloc_dest("swap", do_init=False)
            elif "type-uuid" in part:
                # https://en.wikipedia.org/wiki/GUID_Partition_Table#Partition_type_GUIDs
                if part["type-uuid"] == "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F":
                    # A swap partition
                    dest = _alloc_dest("swap", do_init=False)
                elif part["type-uuid"] == "A19D880F-05FC-4D3B-A006-743F0F84911E":
                    dest = _alloc_dest("RAID", do_init=False)
                elif part["type-uuid"] == "E6D6D379-F507-44C2-A23C-238F2A3DF928":
                    dest = _alloc_dest("LVM", do_init=False)
                    pass
                pass
            else:
                # Let the partition table and "file" command determine
                # the destination and filesystem type, if possible.
                dest = None
                pass

            if "flags" in part:
                for f in part["flags"]:
                    if (f == "boot" or f == "legacy_boot"):
                        boot = True
                        pass
                    if (dest is not None):
                        pass
                    elif (f == "raid"):
                        dest = _alloc_dest("RAID", do_init=False)
                    elif (f == "lvm"):
                        dest = _alloc_dest("LVM", do_init=False)
                    elif (f == "diag"):
                        # What is this?
                        pass
                    elif (f == "swap"):
                        dest = _alloc_dest("swap", do_init=False)
                        pass
                    else:
                        dest = _process_dev_by_contents(name)
                        pass
                    pass
                pass
            part = Partition(p, parent, name, num,
                             line, sectstart, numsects, dest=dest,
                             boot=boot)
            line += 1
            pass
        pass
    return

alldigits = ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")

def _read_fstab(f):
    if (not f):
        return({}, [])

    info = {}
    extra = []
    for l in f:
        s = l.strip()
        if (not s):
            continue
        if (s[0] == "#"):
            extra.append(l)
            continue

        w = l.split()
        if (w[0] == "rootfs"):
            continue

        fs = _valid_filesystem(w[2])
        if (fs is not None):
            info[w[0]] = (w[1], fs, w[3], w[4], w[5])
            continue

        extra.append(l)
        pass

    return (info, extra)

def read_from_file(fn):
    l = ""
    try:
        f = open(fn)
        try:
            l = f.readline().strip()
            pass
        except:
            pass
        close(f)
        pass
    except:
        pass
    return l

def is_a_disk(dev):
    return read_from_file("/sys/block/" + dev + "/device/media") == "disk"

def _add_disks(p, input_fstab):
    startup_errs = ""

    p.popupInfo("Reading disk information, please wait")

    (fstab_info, fstab_extra) = _read_fstab(input_fstab)

    # First look in /proc/diskstats and extract the disks.
    disks = []
    raids = []
    try:
        f = open("/proc/diskstats")
    except Exception as e:
        startup_errs += "Unable to open /proc/diskstats: " + str(e)
        return startup_errs

    try:
        l = f.readline()
        while (l):
            w = l.split()
            if (w[2].startswith("sd") and not w[2].endswith(alldigits)):
                disks.append("/dev/" + w[2])
            elif (w[2].startswith("hd") and not w[2].endswith(alldigits)):
                # Make sure it is actually a disk
                if (is_a_disk(w[2])):
                    disks.append("/dev/" + w[2])
                    pass
                pass
            elif (w[2].startswith("md") and ('p' not in w[2])):
                # Note that the "p" above is important, we need to ignore
                # RAID partitions and just get the main RAID devices.
                raids.append(w[2])
                pass
            l = f.readline()
            pass
        pass
    except Exception as e:
        pass

    # For each disk, query it from parted to get the size of each cylinder
    # and the partitions.
    for d in disks:
        (numsects, sectsize, tabletype, partitions, err) = _disk_info(d)

        if (err is not None):
            startup_errs += err + "\n"
            continue

        if (tabletype is None):
            tabletype = InvalidPartitionTable()
            pass

        disk = Disk(p, d, numsects, sectsize, tabletype)

        if (partitions is None):
            continue

        _process_partitions(p, disk, partitions, d, "", tabletype, fstab_info)
        pass

    # Now handle the raids.
    for r in raids:
        # Find the raid info in /proc/mdstat
        o = open("/proc/mdstat")
        rdevs = []
        found = False
        for l in o:
            if (l[0] == 'm'):
                w = l.split()
                if (w[0] != r):
                    continue
                pass
            else:
                continue

            # We found our raid, extract the info
            found = True
            r = "/dev/" + r
            if (w[2] == "active"):
                level = w[3]
                n = 4
            else:
                level = "inactive"
                n = 3
                pass

            for d in w[n:]:
                d1 = d.split('[')
                rdevs.append("/dev/" + d1[0])
                pass

            break
        
        if (not found):
            startup_errs += "RAID %s not found in /proc/mdstat\n" % r
            continue

        if (level != "inactive"):
            (numsects, sectsize, tabletype, dpartitions, err) = _disk_info(r)
        else:
            numsects = 0
            sectsize = 0
            tabletype = None
            dpartitions = None
            err = None
            pass

        if (tabletype is None and level != "inactive"):
            # No partition table on the MD device, see what else it could be.
            dest = _process_dev_by_fstab(r, fstab_info)
            if (not dest):
                dest = _process_dev_by_contents(r)
                pass
            pass
        else:
            dest = None
            pass

        raid = RAID(p, r, numsects, sectsize, tabletype, dest,
                    level=level)

        for d in rdevs:
            if (d.startswith("/dev/md/")):
                # In case /dev/md/x ends up in the raid detail
                dobj = p.findObj("/dev/md" + d.rsplit("/", 1)[1])
            else:
                dobj = p.findObj(d)
                pass
            if (dobj is None):
                startup_errs += ("Unable to find %s that was in RAID %s\n"
                                 % (d, r))
                continue
            rline = p.findLine(dobj)
            if (dobj.dest.__class__ == RAIDDest):
                dobj.dest.setRAID(p, rline, raid)
                pass
            else:
                # Hmm, it's not already a RAID.  Switch it over
                dobj.newDest(p, RAIDDest(RAIDValue(raid)), rline,
                             doshutdown=False)
                pass
            raid.addVolInit(dobj)
            pass

        if (dpartitions is not None):
            _process_partitions(p, raid, dpartitions, r, "p", tabletype,
                                fstab_info)
            pass
        pass

    # Now LVMs

    # First find the volume groups
    out = _call_lvmdispcmd("vgs")
    lines = out.split("\n")
    for l in lines:
        w = l.split()
        if (not w):
            break
        LVMVG(p, "/dev/" + w[0], int(w[5]), int(w[6]))
        pass

    # Now find all the physical volumes and link them into their volume group
    out = _call_lvmdispcmd("pvs", ["--separator", ":"])
    lines = out.split("\n")
    for l in lines:
        w = l.strip().split(":")
        if (len(w) < 6):
            break
        devname = w[0]
        (pvol, rline) = p.findObjLine(devname)
        if pvol is None:
            startup_errs += "Unable to find LVM PV %s\n" % (devname,)
            continue
        if (len(w[1]) > 0):
            vgdevname = "/dev/" + w[1]
            vg = p.findObj(vgdevname)
            vg.addPVolInit(pvol)
        else:
            vg = None
            pass

        if (pvol.dest.__class__ == LVMDest):
            if (vg is not None):
                pvol.dest.setVG(p, rline, vg)
                pass
            pass
        else:
            # Hmm, it's not already an LVM.  Switch it over
            pvol.newDest(p, LVMDest(LVMValue(vg, do_init=False)), rline)
            pass
        pass

    # Now find all the logical volumes and link them into their volume group
    out = _call_lvmdispcmd("lvs")
    lines = out.split("\n")
    for l in lines:
        w = l.split()
        if (not w):
            break
        vgdevname = "/dev/" + w[1]
        vg = p.findObj(vgdevname)
        devname = vgdevname + "/" + w[0]
        numsects = int(w[3])
        mappername = "/dev/mapper/" + w[1] + "-" + w[0]

        # Try /dev/mapper/vg-lv first
        dest = _process_dev_by_fstab(devname, fstab_info,
                                     realdevname=mappername)
        if (dest is None):
            # Maybe it's /dev/vg/lv
            dest = _process_dev_by_fstab(devname, fstab_info)
            pass
        if (dest is None):
            dest =  _process_dev_by_contents(devname)
            pass
        lvol = LVMLV(p, devname, vg, numsects, dest=dest)
        vg.addLVolInit(lvol)
        pass

    for f in fstab_info:
        w = fstab_info[f]
        startup_errs += (("Filesystem %s mounting %s was found in the fstab"
                          + " but the device wasn't found\n")
                         % (f, w[0]))
        pass

    p.setFstabExtra(fstab_extra)

    p.popupInfoDone()

    return startup_errs

#
#
#
class Partitioner:
    done = False
    
    def __init__(self, parent, input_fstab=None, output_fstab=None):
        self.window = parent

        self.in_reread = False

        self.output_fstab_str = output_fstab
        self.fstab_extra = [ ]

        # Do optimum (True) or minimum (False) alignment.
        self.align_opt = True

        # Display Million bytes by default
        self.units = MiBUnits()

        try:
            curses.curs_set(0) # Turn off the cursor
            #curses.raw()
        except:
            # Some terminals cannot do this.
            pass

        (wheight, wwidth) = parent.getmaxyx()
        self.nlines = wheight - 4
        self.ncols = wwidth
        if (self.nlines < 5):
            raise PartitionerErr("Parent window height of %d is too small,"
                                 + "must be at least 5")
        self.header = parent.derwin(1, wwidth, 0, 0)
        self.footer = parent.derwin(1, wwidth, wheight - 1, 0)
        parent.hline(1, 0, '-', wwidth)
        parent.hline(wheight - 2, 0, '-', wwidth)

        self._drawHeader()
        self._drawCurrInfo()

        parent.refresh()
        self.header.refresh()
        self.footer.refresh()

        self.popup = None

        infstab = None
        errs = ""
        if (input_fstab):
            try:
                infstab = open(input_fstab, "r")
            except Exception as e:
                errs = "Unable to open fstab %s: %s" % (input_fstab, str(e))
                pass
            pass

        self.linepos = 0
        self.colpos = 0
        errs += self.initInfo(infstab)
        if (infstab):
            infstab.close()
            pass
        if (errs):
            self.popupWin(errs)
            pass
        self.sc.refresh()
        return

    def initInfo(self, infstab):
        old_linepos = self.linepos
        # A hash of line entries, indexed by id.
        self.ids = { }

        self.linepos = 0
        self.colpos = 0

        self.sc = FlexScrollColumn.FlexScrollColumn(self.window,
                                                    self.nlines, self.ncols,
                                                    2, 0)

        self.disks = []
        self.diskObj = Label(self, "Disks")
        self.raids = []
        self.raidObj = RAIDLabel(self)
        self.vgs = []
        self.lvmObj = LVMLabel(self)
        
        self.linepos = 0 # Messed up by the previous label adds
        self.sc.highlightColumn(self.linepos, self.colpos)

        return _add_disks(self, infstab)

    def setFstabExtra(self, l):
        self.fstab_extra = l
        return

    def setPos(self, line, col, dir=FlexScrollColumn.LEFT):
        self.sc.unhighlightColumn(self.linepos, self.colpos)
        self.linepos = line
        self.colpos = self.sc.highlightColumn(self.linepos, col, dir)
        return

    def setSizeColumn(self, device, line, col, val):
        self.sc.setColumn(line, col,
                          self.units.convToStr(device, val),
                          rjust=True)
        return

    def deleteLine(self, line):
        o = self.getObj(line)
        self.sc.deleteLine(line)
        del self.ids[str(o)]

        if (line < self.linepos):
            # Need to move our position up
            self.linepos -= 1
        elif (line == self.linepos):
            # Our current line got deleted, need to reposition
            if (self.linepos >= self.sc.numLines()):
                self.linepos = self.sc.numLines() - 1
                pass
            self.colpos = self.sc.highlightColumn(self.linepos, 0)
            pass
        return
    
    def insertLine(self, line, cols, obj):
        self.sc.insertLine(line, cols)
        self.sc.setObj(line, 0, obj)

        self.ids[obj.devname] = obj

        if (line <= self.linepos):
            # Line was added above the current position, need to
            # move it down.
            self.linepos += 1
            pass
        return
    
    def findObj(self, name):
        if (name in self.ids):
            return self.ids[name]
        return None

    def findObjLine(self, name):
        id = self.findObj(name)
        if (id):
            line = self.sc.lineOf(0, id)
        else:
            line = -1
            pass
        return (id, line)

    def findLine(self, obj):
        return self.sc.lineOf(0, obj)

    def numLines(self):
        return self.sc.numLines()

    def getObj(self, line):
        return self.sc.getObj(line, 0)

    def handleCharRaw(self, craw):
        c = CursesKeyMap.keyToStr(craw)
        self.handleChar(c)
        return

    def handleChar(self, c):
        try:
            self._handleChar(c)
        except PartitionerErr as e:
            self.popupWin(str(e))
        except CmdErr as e:
            self.popupWin(str(e))
        except Exception as e:
            (t, v, tb) = sys.exc_info()
            self.popupWin(str(e) + "\n" + "\n".join(traceback.format_tb(tb)))
            pass
        return

    def popupInfo(self, s, reformat=True):
        self.popup = Popup.Popup(self.sc.getWindow(),
                                 self.nlines - 4, self.ncols, 2, 0,
                                 s, None, None, reformat=reformat)
        return
        
    def popupInfoDone(self):
        self.popup = None
        self.redraw()
        return

    def popupWin(self, s, handler=None, handlerObj=None, reformat=True):
        s += "\nPress enter to continue"
        self.popup = Popup.Popup(self.sc.getWindow(),
                                 self.nlines - 4, self.ncols, 2, 0,
                                 s, handler, handlerObj,
                                 reformat=reformat)
        return
        
    def _YesNoCharHandler(self, o, c):
        if (c == "y" or c == "Y"):
            o.result = True
            return True
        elif (c == "n" or c == "N"):
            o.result = False
            return True

        return False

    def _YesNoHandler(self, o):
        o.handler(o.handlerObj, o.result)
        return
        
    def popupYesNo(self, s, handler=None, handlerObj=None):
        o = Obj()
        o.handlerObj = handlerObj
        o.handler = handler
        s += "\nPress 'y' to accept or 'n' to abort."
        self.popup = Popup.Popup(self.sc.getWindow(),
                                 self.nlines - 4, self.ncols, 2, 0,
                                 s, self._YesNoHandler, o,
                                 self._YesNoCharHandler)
        return

    def redraw(self):
        self._drawHeader()
        self.sc.redraw()
        self._drawCurrInfo()
        return

    def redoHighlight(self):
        self.setPos(self.linepos, self.colpos)
        return
        
    def _drawHeader(self):
        s = "%-*s%*s%*s%-*s %s" % (_namelen, " Device Name",
                                 _sizelen, "Start Pos",
                                 _sizelen, "Size",
                                 _typelen, " Type",
                                 "Info")
        self.header.clear()
        self.header.addstr(0, 0, s)
        self.header.refresh()
        return

    def getWork(self):
        work = ()
        for i in range(0, self.numLines()):
            o = self.getObj(i)
            work = work + o.needsWork(self, o)
            pass
        return work
        
    def processWork(self, work, outfstab):
        if outfstab:
            self.output_fstab = outfstab
            for l in self.fstab_extra:
                self.output_fstab.write(l)
                pass
            pass
        for w in work:
            w.work(self)
            pass
        self.output_fstab = None
        return

    def reRead(self):
        """Re-read information from the partition tables, RAIDS,and LVMs"""

        # This keeps filesystems from being written.
        self.in_reread = True
        oldlinepos = self.linepos
        oldcolpos = self.colpos

        # First get our fstab information in a temp file.
        work = self.getWork()
        t = tempfile.TemporaryFile(mode="w+t")
        self.processWork(work, t)

        # Flush the current contents and re-read the partitions using
        # our saved fstab info.
        
        t.seek(0)
        self.initInfo(t)

        t.close()
        self.in_reread = False

        if (oldlinepos >= self.numLines()):
            oldlinepos = self.numlines() - 1
            pass
        self.setPos(oldlinepos, oldcolpos)
        return

    def _drawCurrInfo(self):
        s = "OE Partition/RAID/LVM Manager"
        self.status_in_footer = False
        if (self.align_opt):
            v = "Opt"
        else:
            v = "Min"
            pass
        s += " | aLign: %*s" % (3, v)

        s += " | Units: %-2s" % self.units.name

        self.footer.clear()
        self.footer.addstr(0, 0, s)
        self.footer.refresh()
        return

    def _reUnit(self):
        for i in range(0, self.numLines()):
            o = self.getObj(i)
            o.reUnit(self, i)
            pass
        self._drawCurrInfo()
        return

    def _handleChar(self, c):
        if (self.status_in_footer):
            self._drawCurrInfo()
            pass
        if (self.popup):
            handled = self.popup.handleChar(c)
            if (not handled):
                self._status("Unknown key pressed: '%s'" % c)
                pass
            if (self.popup and self.popup.done):
                self.popup = None
                self.redraw()
                pass
            return
        
        if (c == 'Q'):
            work = self.getWork()

            # Do any of the work object write anything?  If so, query if
            # the user really wants to do them.
            write_op = False
            for w in work:
                if (w.write_op):
                    write_op = True
                    pass
                pass
            if (write_op):
                self.popupYesNo("All filesystems that are set will be written"
                                + " to the disk.  Are you sure you want to"
                                + " do this?", self.queryQuitDone, work)
            else:
                self.queryQuitDone(work, True)
                pass
            pass
        elif (c == 'L'):
            self.align_opt = not self.align_opt
            self._drawCurrInfo()
        elif (c == 'U'):
            self.units = self.units.allocNext()
            self._reUnit()
        elif (c == 'P'):
            for d in self.disks + self.raids:
                try:
                    _reread_partition_table(d)
                except CmdErr as e:
                    pass
                pass
            pass
            self.reRead()
        elif (c == '^L'):
            self.redraw()
        elif (c == '?'):
            self.popupWin(_help_text, reformat=False)
        elif (c == 'UP'):
            if (self.linepos > 0):
                self.setPos(self.linepos - 1, self.colpos)
                while (self.linepos < self.sc.getFirstDisplayedLine()):
                    self.sc.scrolly(-1)
                    pass
                pass
            pass
        elif (c == 'DOWN'):
            if (self.linepos < self.sc.numLines() - 1):
                self.setPos(self.linepos + 1, self.colpos)
                while (self.linepos > self.sc.getLastDisplayedLine()):
                    self.sc.scrolly(1)
                    pass
                pass
            pass
        elif (c == 'LEFT'):
            self.setPos(self.linepos, self.colpos - 1, FlexScrollColumn.LEFT)
        elif (c == 'RIGHT'):
            self.setPos(self.linepos, self.colpos + 1, FlexScrollColumn.RIGHT)
        elif (c == 'NPAGE'):
            lline = self.sc.getLastDisplayedLine()
            if (lline >= self.sc.numLines()):
                lline = self.sc.getLastDisplayedLine()
                if (lline >= self.sc.numLines()):
                    lline = self.sc.numLines() - 1
                    pass
                self.setPos(lline, self.colpos, FlexScrollColumn.LEFT)
            else:
                self.sc.scrolly(self.sc.getNumDisplayLines())
                self.setPos(self.sc.getFirstDisplayedLine(), self.colpos,
                            FlexScrollColumn.LEFT)
                pass
            pass
        elif (c == 'PPAGE'):
            if (self.sc.getFirstDisplayedLine() == 0):
                self.setPos(0, self.colpos, FlexScrollColumn.LEFT)
            else:
                self.sc.scrolly(-self.sc.getNumDisplayLines())
                lline = self.sc.getLastDisplayedLine()
                if (lline >= self.sc.numLines()):
                    lline = self.sc.numLines() - 1
                    pass
                self.setPos(lline, self.colpos, FlexScrollColumn.LEFT)
                pass
            pass
        else:
            # Call the object at the given line and column.  If it does not
            # handle the request, try the object at column 0 for general
            # handling.
            rv = False
            obj = self.sc.getObj(self.linepos, self.colpos)
            if (self.colpos != 0 and obj is not None):
                rv = obj.Command(self, c)
                pass
            if (not rv):
                obj = self.sc.getObj(self.linepos, 0)
                rv = obj.Command(self, c)
                pass
            if (not rv):
                self._status("Unknown key pressed: '%s'" % c)
                pass
            pass
        return
    
    def queryQuitDone(self, work, val):
        if (not val):
            self.popupYesNo("There are unformatted filesystem you have"
                            + " chosen, do you really want to quit?",
                            self.queryQuit2Done, work)
            return
        outfstab = None
        if (self.output_fstab_str):
            outfstab = open(self.output_fstab_str, "w")
            pass
        self.processWork(work, outfstab)
        if (outfstab):
            outfstab.close()
            pass
        self.done = True
        return

    def queryQuit2Done(self, work, val):
        if (not val):
            return
        self.done = True
        return

    def _status(self, str):
        self.status_in_footer = True
        self.footer.clear()
        self.footer.addstr(0, 0, str)
        self.footer.refresh()
        return

    # The rest are wrapper functions for FlexColumnScroll
    def setObj(self, line, col, obj):
        self.sc.setObj(line, col, obj)
        return

    def setColumn(self, line, col, val, obj=FlexScrollColumn.dummyObj,
                  rjust=False):
        self.sc.setColumn(line, col, val, obj, rjust)
        return

    def getWindow(self):
        return self.sc.getWindow()

    def recolumn(self, line, col, columns):
        self.sc.recolumn(line, col, columns)
        return

    def numLines(self):
        return self.sc.numLines()

    def lineOf(self, col, obj):
        return self.sc.lineOf(col, obj)

    def refresh(self):
        self.sc.refresh()
        return

    pass

_help_text = """               OE Partition/RAID/LVM Management
               ----------------------------------------

Note: Press "Enter" to leave this help.

Welcome to the OE Partition/RAID/LVM Management tool.  This
tool allows you to partition the disks, add RAIDs and LVMs, and set up
filesystems and swap devices for a OE Linux system you are
about to install onto a target.

The tool has a top line of labels for the columns below and a status
line at the bottom of the screen.  In the middle is the list of actual
devices to be managed.  Three types of devices are managed: disks,
RAIDs, and LVM devices.  These are labeled with these names starting
at the leftmost of the screen, and those devices are indented under
their labels.

NOTE: In this tool, most operations occur immediately when you execute
them.  So when you update partition tables, add devices to RAIDs or
LVMs, or delete devices, those operations occur immediately.  This is
perhaps somewhat different from other tools, but for disk partitions
to be ready to add to RAIDs and LVMs, they really need to be created.
The exception to this is filesytems and swap devices.  They are created
at the end when you quit, because sometimes those operations can take
a long time.

Important: All commands are capital letters.  So it's "A" to add, not
"a".  This will keep you from accidentally entering commands if you think
you are typing a name.

To quit the installer, use the "Q" (that's shift-q) key.  

Single characters cause operations on the various devices.  Different
devices have different commands.  You operate on a device by moving the
cursor to that device and pressing the key for the command.  Cursor keys
and the page up and page down keys move you around.  The cursor can always
be moved to the device name column of a device.  Other columns can only
take a cursor if they can be modified by the user.

Sometimes background operations (RAID array syncing, for instance) can
cause console output.  If that happens, the Ctrl-L command will cause
the screen to be redrawn.

If you want to re-read all the partition tables, use the "P" command.
Note that you will need to quite and re-enter the partitioner to see
the changed partitions.

Values that can be modified by the user are selected by pressing the
"Enter" key when that column has the cursor.  This will pop up a list
or box that allows the user to select an item or enter some data.

Each device has a "Type" under that column.  Different devices have
different valid types.  To the right of the Type is information about
the type and possibly selectable items.

All devices that have some sort of start position on a disk (primarily
partitions) have a "Start Pos" column set.  All devices have a "Size"
column set telling how much space is on the device.

When the tool comes up, it displays everything in megabytes,
represented by "Units: Mi" at the bottom of the screen and a M after
the values.  The "U" command changes units, other units are Gigabytes
(Gi), Kilobytes (Ki) and Sectors (S).  Gigabytes and Kilobytes show a
K or G after the values, Sectors shows nothing.  When entering values,
the letter after the value is optional, but the current units is
always used.

The tool supports aligning the sectors on various boundaries.  Two
alignments are supported, minimum, shown by "aLign: Min" at the bottom
of the screen, or optimal, shown by "aLign: Opt" at the end of the
screen.  The default is optimal, devices will generally have an
alignment that gives the best performance.  The tool will fetch that
value and use it.  When inputting values in sector mode, misaligned
values will not be allowed and you will be re-prompted with values
that are properly aligned.  In the other modes, the chosen values are
automatically re-aligned to meet the alignment constraints.  The "I"
command on a disk or RAID device will show the alignments.

Flow of Operation
-----------------

For best efficiency, the user should do things in the following order when
installing:
   * Create any RAID devices required.
   * Create any volume groups required.
   * Create partitions and assign them to RAID devices and/or volume
     groups if required.
   * Assign RAID devices to volume groups.
   * Create any logical volumes required.

This is just a suggested flow, the user can do these things in any
order that works.  Note that you obviously cannot create logical
volumes from volume groups that have no partitions or RAIDs assigned
to them, nor can you assign RAID devices to volume groups if those RAID
devices have no partitions assigned to them.  A RAID device or volume
group with no contents doesn't actually exist, you must assign data
to them to bring them into existence.

Note that though RAID devices can be partitioned, and extended partitions
can be used, their use is discouraged.  LVM should be used to break up
disk and RAID devices.

It is suggested that you create two disk partitions on the first drive
(or first two drives if using RAID), one with about 100MB for the /boot
partition and one assigned to an LVM device.  Then use the LVM device
to create logical volumes for the / filesystem and any other filesystems
required.  Then create a single partition on other disks and use LVM
to manage them.  Note that you can put multiple disks into a single
LVM, so you can create a very large LVM volume group, if required.

Disks
-----

Each disk in the system is listed here, indented by one space under
the "Disks" label.  The only valid type of a disk is "part", or
partition.  Valid commands for a disk are:
  A - Add a new primary partition to the disk.
  E - Add an extended partition to the disk (only valid on MSDOS partitions).
  I - Pop up some extra info about the disk.
See the section on Partition Type for more details

Each primary disk partition is listed indented by one under the disk
that owns it.  Valid commands for partitions are:
  D - Delete the partition
  B - Toggle the boot flag on the partition
Note that if a partition is bootable, it has a "b" at the very left
of it's line.  Valid types for a partition are: fs (Filesystem),
RAID, LVM, or swap.

Extended disk partitions are special partitions that hold more
partitions beyond the standard four primary partitions.  These start
numbering at "5" and are call logical partitions.  The only valid type
for extended partitions is "ext".  The logical partitions are indented
under the extended partition that contains them.  Note that a disk may
only have one extended partitions.  Valid commands for extended partitions
are:
  A - Add a logical partition to the device.
  D - Delete the extended partition
Note that logical partitions are always numbered contiguously starting
at "5".  So if you delete an extended partition, all devices with a
larger number will be decremented by one.  For instance, if a device
has logical partitions 5, 6, 7, and 8, and you delete partition 6,
partition 7 will become partition 6 an partition 8 will become
partition 7.

* Use of extended partitions is discouraged.  LVM provides more
  powerful and easy to use disk management.

Logical partitions behave just as primary partitions.

RAID
----

RAID devices let multiple disk partitions be mirror images of each
other, and the allow the same disk that appears on multiple paths to
be represented as a single disk.  For a mirror, if a disk fails, all
of the data is mirrored on another disk and the system can continue
operating without loss of service.  For multipath, if a path fails,
then the other path can take over to talk to the disk.  When a new
device/path is inserted to replace the failed on, it can be re-added
into the raid to restore the redundancy.  Note that only RAID1 and
multipath is supported by the tool at this time.

To add a disk partition to a RAID1 device, first create the RAID
device using the "A" command on the "RAIDs" label.  The default is
raid1.  Then move to the Type of the disk partition, press Enter, and
choose RAID from the list.  Then move to the column to the right of
the type (note it may be blank), press Enter, and choose the RAID
device you added.  You can add more than one partition to a RAID, each
partition will be a mirror of the others.  The RAID device's size will
be the size of the smallest partition.

By default the RAID device is created as raid1.

RAIDs have the same valid types as a primary partition plus they have
a "part" type, which makes the RAID partitionable.  RAID partitions
appear as /dev/mdNpM, where N is the RAID number and M is the partition
number.  See the Partition Type section for more details on partitions.

RAID devices support the following commands:
  A - Add a new primary partition to the RAID (only for partition types).
  D - Delete the RAID device
  E - Add an extended partition to the RAID (only valid on MSDOS partitions).
  I - Pop up some extra info about the RAID.

If you don't add any partition devices to a RAID, it will not actually
be created because the metadata for the RAID has to be on a device
somplace.  So if you quit, the device will not actually exist when you
restart.

Multipath
---------

Multipath is when you have multiple paths to the same disk.  These
appear in Linux as two different disks.  In order to combine them to
appear as a single disk you can use, but with redundancy of path, you
need to partition the drives and put the partitions into multipath
RAIDs.

Note that multipath is done on partitions, not whole disks.  This may
seem a little strange, but it allows the same tools to be used for
RAID and multipath, and it allows automatic assembly of multipath.
udev is generally used to add and remove the volumes, just like normal
RAID.

For multipath, you can use the "I" command on individual disks to
find the serial number of the disk.  The two disks with the same
serial number are the same disk.

THIS IS VERY IMPORTANT:  Add a partition table for ONE of the disks.
The use the "P" command to force the system to reread the partition
table.  Quit the partitioner and restart it to reread the new
partitions tables.  Both disks will have a partition table.

To add a multipath partition, add a RAID device, then set the level
(just to the right of the device name) to "inactive".  Then add
the devices to the RAID as with RAID1.  When all the devices have
been added, change the RAID level to "multipath".  You cannot
resize multipath RAID volumes, to change them you must set them
inactive first.  Make sure to add the volume for both paths, adding
just one is not enough.

The partitioner checks to make sure all elements of a multipath RAID
are partitions, on the same disk, and have the same partition number.

LVM
---

Logical volume management give an easy and powerful way to manage
space on a disk.  A Volume Group (VG) consists of one or more RAID or
partition devices.  The devices in a VG are called Physical Volumes
(PVs).  These devices do not need to be on the same physical disk, so
a VG can be larger than the disks in the system.  A VG can be divided
up into Logical Volumes (LVs) that can then be used as filesystem or
swap destinations.

PVs can be dynamically added to and removed from a VG.  This way, if
you add more disks to a system, those disks can be added to existing
VGs to increase their size.  LVs can be dynamically resized, too.  So
if an LV is too small, it can be made larger and if it is too big it
can be made smaller.  If a filesystem type is chosen that supports
dynamic resizing, this can be done while the system is running.

Each VG is listed under the LVMs label indented by one.  The LVs owned
by the VG are indented under the VG.

To add a disk partition or RAID device to a VG, first create the VG
device using the "A" command on the "LVMs" label.  Then move to the
Type of the disk partition or RAID, press Enter, and choose LVM from
the list.  Then move to the column to the right of the type (note it
may be blank), press Enter, and choose the LVM VG you added.  You can
add more than one partition to an LVM, each partition will add to the
LVM's size.

Valid commands for a VG are:
  A - Add a LV to the VG
  D - Delete the VG (and all contained LVs)

Valid commands for LVs are:
  D - Delete the LV

When adding a VG, it will ask for the VG name.  The does not include
the "/dev/" that is in the device name, so if you create a VG named
"vg01", the device name will be /dev/vg01.

When adding a LV to a VG, it will ask for the LV name and a size.
Logical volumes are /dev/mapper/<VG>-<LV>, where <VG> is the VG name
and <LV> is the LV name you choose.  However, due to space constraints
on the screen, only the <LV> value you choose is displayed.


Partition Type
--------------

All disks are partitioned, and RAIDs may be partitions.  To set a RAID
partitionable, set the RAID's type to "part".

To the right of the "part" type on a disk or RAID is the partition type.
Valid partitions types are:
  "<inv>" - The device has no recognizable partition table
  "<???>" - The device has a partition table the tool does not support
  "MSDOS" - A standard MSDOS partition table
  "GPT"   - A standard GUID partition table

This tool supports two partition table types: MSDOS and GUID Partition
Table (GPT).  MSDOS partitions tables are generally required for
booting from most BIOSes.  However, they only support 4 primary
partitions without extended partitions.  GPTs support up to 128
partitions on a disk without extended partitions and are supported by
EFI and some BIOSes.

When adding a partition, the tool picks the first free area on the
disk that can fit the current alignment constraints and displays that
in a prompt.  The user can then edit the values.  This can be a little
confusing if there is a small disk area free at the start of the disk.
The small are will be displayed, not the larger area that might be
available farther down the disk.

When editing in sector mode, if the values to fit the alignment
constraints they will be rejected and the prompt re-displayed with the
values at the next available alignment.  In the other unit modes, the
values will be automatically aligned to the closest alignment value.

Filesystem and Swap Types
-------------------------

Partitions that are newly created or that are set as a Linux
filesystem type are display as type "fs".  This is for holding a Linux
filesystem.  The filesystem chosen is left blank, and in that case the
tool will not do anything to the partition.

If a filesystem is chosen for the device, when the tool exits it will
prompt you to see if you want to format those devices.  If you choose
yes, at that time the devices will be formatted.  The same goes for
swap devices.

To the right of the chosen filesystem you may choose a mount point
(that is initially blank).  If you do this, the installer will pick up
these mount points when creating /etc/fstab so it know where to mount
the various devices.

Suggested Configuration
-----------------------

In a non-RAID system, it is suggested that you create two partitions:
a partition to mount as "/boot" that's about 100M or so at the
beginning of the first disk.  Then create a partition with the rest of
the disk and a single partition on each other disks.  Add these
partitions to a VG, and use the VG to create LVs for the various
mount points you need.  This gives maximum flexibility.

For a RAID system, the suggested configuration is similar, but create
two partitions on different disks for mounting on "/boot" and RAID
them.  Then create a partition on the rest of each of those disks and
RAID them, then RAID each pair of other disks.  Then take those RAID
devices and add them to a VG and use the VG to create the LVs you need.

Make sure to set the /boot partition(s) bootable with the "B" command.

The exact configuration depends on your needs, of course.
"""

def partition(stdscr, argv):
    output_fstab = None
    input_fstab = None
    for i in argv:
        if (output_fstab == ""):
            output_fstab = i
            continue
        if (input_fstab == ""):
            input_fstab = i
            continue

        if i == "--output-fstab":
            output_fstab = "" # Mark for next iteration
            pass
        elif i == "--input-fstab":
            input_fstab = "" # Mark for next iteration
            pass
        else:
            pass
        pass

    p = Partitioner(stdscr, input_fstab=input_fstab, output_fstab=output_fstab)
    while (not p.done):
        c = stdscr.getch()
        p.handleChar(CursesKeyMap.keyToStr(c))
        p.refresh()
        pass
    return

if __name__== '__main__':
    curses.wrapper(partition, sys.argv)
    pass

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

from distutils.core import setup
setup(name='mvpartition',
      version='0.1',
      description='MontaVista Disk Partitioner',
      author='MontaVista Software, LLC',
      author_email='source@mvista.com',
      url="http://www.mvista.com",
      packages=['mvpartition'],
      scripts=['bin/mvpartition']
      )

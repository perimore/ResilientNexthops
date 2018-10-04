#!/usr/bin/env python
# Copyright (c) 2018 Arista Networks, Inc.  All rights reserved.
# Arista Networks, Inc. Confidential and Proprietary.

import eossdk
import sys
import syslog
import re
import time
import itertools
from operator import itemgetter

NexthopList = []

class ResilientNexthopHandler( eossdk.AgentHandler, eossdk.NeighborTableHandler ):
   def __init__( self, neighborMgr, nexthopGroupMgr, agentMgr ):
      # Initialise the handlers
      eossdk.AgentHandler.__init__( self, agentMgr )
         # pylint: disable-msg=W0233
      eossdk.NeighborTableHandler.__init__( self, neighborMgr )
         # pylint: disable-msg=W0233
      self.tracer = eossdk.Tracer("ResilientNexthopAgent")
      self.agentMgr = agentMgr
      self.neighborMgr_ = neighborMgr
      self.nexthopGroupMgr_ = nexthopGroupMgr
      syslog.syslog("Constructed")
      self.tracer.trace0("Constructed")

   # Callback provided by AgentHandler when all state is synchronized
   def on_initialized( self ):
      # Called when the agent is initialised to set the status
      lastChangeTime = re.sub( ' +', ' ', time.ctime() )
      self.agentMgr.status_set("Agent Status:", "Administratively Up (Since: "
         +str(lastChangeTime)+")")
      self.watch_all_neighbor_entries( True )  # pylint: disable-msg=E1101
      self.tracer.trace0("We are initialized!" )
      syslog.syslog( "We are initialized!" )
      self.BuildNexthopList()

   def on_agent_option( self, option, value ):
      self.tracer.trace0("Nexthop configuration changed: (" + str(option) + "," 
         + (str(value) if str(value) != "" else "Removed") + ")")  
      self.BuildNexthopList()
      
   def BuildNexthopList(self):
      # Create a list of the daemon options containing the nexthops to load balance
      # across
      global NexthopList
      NexthopList = []
      for nhEntry in self.agentMgr.agent_option_iter():
         # [ Entry Number, IP Address, Active ]
         NexthopList.append( [int(nhEntry), self.agentMgr.agent_option(nhEntry),
            False] )

      # If we have any configured nexthops, continue.
      if len( NexthopList ) > 0:
         self.tracer.trace3( "Current order of nexthops: " + str(NexthopList))
         NexthopList.sort(key=itemgetter(0))
         self.tracer.trace3( "Sorted order of nexthops: " + str(NexthopList) )
         # Setup the nexthop group
         self.BuildNexthopGroup()
      else:
         syslog.syslog( "Nexthops not defined. Check config." )
         self.tracer.trace0( "Nexthops not defined. Check config." )

   def BuildNexthopGroup(self):
      global NexthopList
      # Calculate the lowest common multiple of the nexthop entries
      # This allows the script to fairly distribute traffic in the event of a
      # nexthop failure.
      possibleNexthops = range(len(NexthopList), 0, -1)
      numNexthops = self.lcmm(*possibleNexthops)
      # Create a nexthop group object
      nexthopGroup = eossdk.NexthopGroup("NH1", eossdk.NEXTHOP_GROUP_IP)

      for entry, ip, active in NexthopList:
         # Create a neighbour key using the nexthop IP
         neighbor_key = eossdk.NeighborKey(
            self.get_ip_addr(ip), eossdk.IntfId())
         # Try and find the neighbour entry using the neighbour key
         neighbor_entry = self.neighborMgr_.neighbor_entry_status(neighbor_key)
         # If an entry is found, get the actual entry
         if neighbor_entry == eossdk.NeighborEntry():
            neighbor_entry = self.neighborMgr_.neighbor_entry(neighbor_key)
         # If no entry is returned, it must not be active
         if neighbor_entry == eossdk.NeighborEntry():
            NexthopList[entry][2] = False
         # Else it is is active, set the active flag
         else:
            NexthopList[entry][2] = True

      self.tracer.trace3("Nexthop entries: " + str(NexthopList))

      # Build the successful Nexthops
      x = 0
      # Create a list of active and failed nexthops
      activeNexthops = []
      failedNexthops = []
      # Loop the least common multiplier of the number of nexthops
      for i in range(numNexthops):
         # If the entry is active, set the ip as an eossdk.IpAddr object
         if NexthopList[i-x][2]:
            ip = self.get_ip_addr(NexthopList[i-x][1])
            # If we got an IpAddr object back, create a nexthop entry
            if ip is not None:
               nexthopGroup.nexthop_set(i, eossdk.NexthopGroupEntry(ip))
               # Add the IP address to the active nexthops
               activeNexthops.append(NexthopList[i-x][1])
         else:
            # Add the failed nexthop entry number to a new array.
            # We will cycle this later
            failedNexthops.append(i)
         # Increment x if we have reached the end of the configured nexthops but
         # we have expanded to the LCM
         if i==((len(NexthopList)-1) + x) : x += len(NexthopList)

      self.tracer.trace3("Failed nexthops: " + str(failedNexthops)) if \
         len(failedNexthops) > 0 else self.tracer.trace3("Failed nexthops: None")
      self.tracer.trace3("Active nexthops: " + str(activeNexthops)) if \
         len(activeNexthops) > 0 else self.tracer.trace3("Active nexthops: None")
      # If there are failed entries, fill in the gaps if we have active nexthops
      if len(failedNexthops) > 0 and len(activeNexthops) > 0:
         # Allow the active nexthops to be iterated cyclicly
         nexthopEntry = itertools.cycle(activeNexthops)
         # For each failed entry, fill in with the next active entry
         for entry in failedNexthops:
            activeNexthop = next(nexthopEntry)
            ip = self.get_ip_addr(activeNexthop)
            nexthopGroup.nexthop_set(entry, eossdk.NexthopGroupEntry(ip))

      # If there are active nexthops, create the nexthop-group. Otherwise, remove it.
      # This allows the static route pointing to the nexthop-group to become
      # inactive.
      if len(activeNexthops) > 0:
         self.nexthopGroupMgr_.nexthop_group_set(nexthopGroup)
      elif self.nexthopGroupMgr_.exists('NH1'):
         self.nexthopGroupMgr_.nexthop_group_del('NH1')

   def get_ip_addr(self, ip_addr):
      try:
         return eossdk.IpAddr(ip_addr)
      except eossdk.Error as e:
         sys.stderr.write('Invalid IP address: %s (%s)' % (ip_addr, e))

   # Callback provided by NeighbourHandler when an ARP entry appears
   def on_neighbor_entry_set( self, neighborEntry ):
      entry = neighborEntry
      entryIp = entry.neighbor_key().ip_addr().to_string()
      update = False
      nexthopGroup = self.nexthopGroupMgr_.nexthop_group("NH1")
      for i in NexthopList:
         if i[1] == entryIp:
            update = True
      if update:
         self.BuildNexthopGroup()

   # Callback provided by NeighbourHandler when an ARP entry is deleted
   def on_neighbor_entry_del( self, neighborKey ):
      entry = neighborKey
      entryIp = entry.ip_addr().to_string()
      update = False
      nexthopGroup = self.nexthopGroupMgr_.nexthop_group("NH1")
      for i in NexthopList:
         if i[1] == entryIp:
            update = True
      if update:
         self.BuildNexthopGroup()
        
   # Calculate the Lowest Common Multiplier
   def lcm(self, x,y):
      tmp=x
      while (tmp%y)!=0:
         tmp+=x
      return tmp

   def lcmm(self, *args):
      return reduce(self.lcm,args)

sdk = eossdk.Sdk()
mta = ResilientNexthopHandler( sdk.get_neighbor_table_mgr(),
   sdk.get_nexthop_group_mgr(), sdk.get_agent_mgr() )
sdk.main_loop( sys.argv )
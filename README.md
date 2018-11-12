The ResilientNexthops application keeps the total number of nexthops the same by 
replacing the deleted entry with one or more of the active entries to maintain the 
order of entries in the nexthop-group.

The configuration to achieve resilient nexthop behaviour is shown below:
```
daemon ResilientNexthops
   exec /mnt/flash/ResilientNexthops
   option 0 value <Nexthop via FW1>
   option 1 value <Nexthop via FW2>
   option 2 value <Nexthop via FW3>
   option GROUP_NAME value NextHopGroup1
   no shutdown
```

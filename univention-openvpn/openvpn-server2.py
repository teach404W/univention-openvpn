#
#       Univention OpenVPN integration -- openvpn-server2.py
#


__package__ = ''  # workaround for PEP 366

import listener
import univention.debug
import re
import univention_baseconfig
import os
import csv
import univention.uldap as ul
from netaddr import *

name        = 'openvpn-server2'
description = 'manage fixed ip addresses on user actions'
filter      = '(objectClass=univentionOpenvpnUser)'
attribute   = []
modrdn      = 1

action = None

fn_serverconf = '/etc/openvpn/server.conf'

# function to open a textfile with setuid(0) for root-action
def load_rc(ofile):
    l = None
    listener.setuid(0)
    try:
        f = open(ofile,"r")
        l = f.readlines()
        f.close()
    except Exception, e:
        univention.debug.debug(univention.debug.LISTENER, univention.debug.ERROR, 'Failed to open "%s": %s' % (ofile, str(e)) )
    listener.unsetuid()
    return l

# function to write to a textfile with setuid(0) for root-action
def write_rc(flist, wfile):
    listener.setuid(0)
    try:
        f = open(wfile,"w")
        f.writelines(flist)
        f.close()
    except Exception, e:
        univention.debug.debug(univention.debug.LISTENER, univention.debug.ERROR, 'Failed to write to file "%s": %s' % (wfile, str(e)))
    listener.unsetuid()

# function to delete a textfile with setuid(0) for root-action
def delete_file(fn):
    listener.setuid(0)
    try:
        os.remove(fn)
    except Exception, e:
        univention.debug.debug(univention.debug.LISTENER, univention.debug.ERROR, 'Failed to remove file "%s": %s' % (fn, str(e)))
    listener.unsetuid()

# function to delete a directory with setuid(0) for root-action
def delete_dir(fn):
    listener.setuid(0)
    try:
        os.rmdir(fn)
    except Exception, e:
        univention.debug.debug(univention.debug.LISTENER, univention.debug.ERROR, 'Failed to remove file "%s": %s' % (fn, str(e)))
    listener.unsetuid()

# function to open an ip map with setuid(0) for root-action
def load_ip_map(path):
    ip_map = []
    listener.setuid(0)
    try:
        with open(path, 'rb') as f:
            r = csv.reader(f, delimiter=' ', quotechar='|')
            for row in r:
                ip_map.append(row)
    except Exception, e:
        univention.debug.debug(univention.debug.LISTENER, univention.debug.ERROR, 'Failed to load ip map: %s' % str(e))
    listener.unsetuid()
    return ip_map

# function to write an ip map with setuid(0) for root-action
def write_ip_map(ip_map, path):
    listener.setuid(0)
    try:
        with open(path, 'wb') as f:
            w = csv.writer(f, delimiter=' ', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            for i in ip_map:
                w.writerow(i)
    except Exception, e:
        univention.debug.debug(univention.debug.LISTENER, univention.debug.ERROR, 'Failed to write ip map: %s' % str(e))
    listener.unsetuid()

def handler(dn, new, old, command):
    global action
    if command == 'n':
        action = None
        return

    myname = listener.baseConfig['hostname']

    listener.setuid(0)
    lo = ul.getBackupConnection()
    server = lo.search('(cn=' + myname + ')')[0]
    listener.unsetuid()
    port = server[1].get('univentionOpenvpnPort', [None])[0]
    network = server[1].get('univentionOpenvpnNet', [None])[0]
    netmask = str(IPNetwork(network).netmask)
    networkv6 = server[1].get('univentionOpenvpnNetIPv6', [None])[0]
    if networkv6 is None:
        networkv6 = "2001:db8:0:123::/64"
    netmaskv6 = str(IPNetwork(networkv6).netmask)

    ccd = '/etc/openvpn/ccd-' + port + '/'
    fn_ips = '/etc/openvpn/ips-' + port
    fn_ipsv6 = '/etc/openvpn/ipsv6-' + port

    if not os.path.exists(ccd):
        os.makedirs(ccd)
        ip_map = load_ip_map(fn_ips)
        for (name, ip) in ip_map:
            line = "ifconfig-push " + ip + " " + netmask
            write_rc(line, ccd + name + ".openvpn")

    if not os.path.exists(fn_ips):
        listener.setuid(0)
        open(fn_ips, 'a').close()
        listener.unsetuid()

    if not os.path.exists(fn_ipsv6):
        listener.setuid(0)
        open(fn_ipsv6, 'a').close()
        listener.unsetuid()

    # delete entries and ready2go packet in /var/www/ on user deletion
    if command == 'd':
        client_cn = old.get('uid', [None])[0]

        delete_file(ccd + client_cn + ".openvpn")
        delete_file("/var/www/" + client_cn + "/.htaccess")
        delete_file("/var/www/" + client_cn + "/openvpn-" + myname + "-" + client_cn + ".zip")
        delete_dir("/var/www/" + client_cn + "/")

        delete_entry(client_cn, fn_ips)
        delete_entry(client_cn, fn_ipsv6)

        return

    client_cn = new.get('uid', [None])[0]

    # generate and write entries on account activation
    if 'univentionOpenvpnAccount' in new and not 'univentionOpenvpnAccount' in old:
        lines = []

        ip = write_entry(client_cn, fn_ips, network)
        ipv6 = write_entry(client_cn, fn_ipsv6, networkv6)

        lines.append("ifconfig-push " + ip + " " + netmask + "\n")
        lines.append("ifconfig-ipv6-push " + ipv6 + "/" + networkv6.split('/')[1] + "\n")

        write_rc(lines, ccd + client_cn + ".openvpn")

        return

    # delete entries on account deactiviation
    elif not 'univentionOpenvpnAccount' in new and 'univentionOpenvpnAccount' in old:
        delete_file(ccd + client_cn + ".openvpn")
        delete_entry(client_cn, fn_ips)
        delete_entry(client_cn, fn_ipsv6)

        return

# generate and write entry for given user and return generated ip
def write_entry(client_cn, fn_ips, network):
    ip_map = load_ip_map(fn_ips)
    ip = generate_ip(network, ip_map)
    ip_map.append((client_cn, ip))
    write_ip_map(ip_map, fn_ips)
    return ip

# delete entry of given user in corresponding ip_map
def delete_entry(client_cn, fn_ips):
    ip_map_old = load_ip_map(fn_ips)
    ip_map_new = []
    for (name, ip) in ip_map_old:
        if name != client_cn:
            ip_map_new.append((name, ip))
    write_ip_map(ip_map_new, fn_ips)

# generate ip for given network which does not exist in ip_map
def generate_ip(network, ip_map):
    ips = IPNetwork(network)
    first = ips[0]
    second = ips[1]
    for newip in ips.iter_hosts():
        if newip == first or newip == second:
            continue
        use = True
        for (name, ip) in ip_map:
            if str(newip) == ip:
                use = False
                break
        if use:
            return str(newip)

def initialize():
    pass

def postrun():
    global action
    if not action:
        return
    univention.debug.debug(univention.debug.LISTENER, univention.debug.INFO, 'OpenVPN-Server %s' % (action))

    try:
        listener.setuid(0)
        listener.run('/etc/init.d/openvpn', ['openvpn', action], uid=0)
    finally:
        listener.unsetuid()

    if action == 'stop':
        # deactivate config
        try:
            listener.setuid(0)
            os.rename (fn_serverconf, fn_serverconf + '-disabled')
        except Exception, e:
            listener.unsetuid()
            univention.debug.debug(univention.debug.LISTENER, univention.debug.ERROR, 'Failed to deactivate server config: %s' % str(e))
            return

    listener.unsetuid()


### end ###

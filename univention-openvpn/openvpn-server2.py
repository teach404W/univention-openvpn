#
#       Univention OpenVPN integration -- openvpn-server2.py
#

# Copyright (c) 2014-2015, bytemine GmbH
# All rights reserved.
#
# Redistribution and use in source and binary forms, with
# or without modification, are permitted provided that the
# following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
# OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
# TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

__package__ = ''  # workaround for PEP 366

import listener
import univention.debug as ud
import re
import univention_baseconfig
import os
import csv
import univention.uldap as ul
from netaddr import *

from datetime import date
from M2Crypto import RSA, BIO
from base64 import b64decode


name        = 'openvpn-server2'
description = 'manage fixed ip addresses on user actions'
filter      = '(objectClass=univentionOpenvpnUser)'
attributes  = ['univentionOpenvpnAccount']
modrdn      = 1

action = None

fn_serverconf = '/etc/openvpn/server.conf'


pubbio = BIO.MemoryBuffer('''
-----BEGIN PUBLIC KEY-----
MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBAN0VVx22Oou8UTDsrug/UnZLiX2UcXeE
GvQ6kWcXBhqvSUl0cVavYL5Su45RXz7CeoImotwUzrVB8JnsIcrPYw8CAwEAAQ==
-----END PUBLIC KEY-----
''')
pub = RSA.load_pub_key_bio(pubbio)
pbs = pub.__len__() / 8

def license(key):
  try:
    enc = b64decode(key)
    raw = ''
    while len(enc) > pbs:
      d, key = (enc[:pbs], enc[pbs:])
      raw = raw + pub.public_decrypt(d, 1)
    if len(enc) != pbs:
      return None		# invalid license
    raw = raw + pub.public_decrypt(enc, 1)
    #
    items = raw.rstrip().split('\n')
    if not items:
      return None 		# invalid license
    vdate = int(items.pop(0))
    if date.today().toordinal() > vdate:
      ud.debug(ud.LISTENER, ud.ERROR, '4 License has expired')
      return None		# expired
    l = {'valid': True, 'vdate': vdate} # at least one feature returned
    while items:
      kv = items.pop(0).split('=', 1)
      kv.append(True)
      l[kv[0]] = kv[1]
    return l			# valid license
  except:
    return None			# invalid license

def maxvpnusers(key):
  mnlu = 5
  try:
    return max(int(license(key)['u']), mnlu)
  except:
    ud.debug(ud.LISTENER, ud.ERROR, '4 Invalid license')
    return mnlu			# invalid license


# function to open a textfile with setuid(0) for root-action
def load_rc(ofile):
    l = None
    listener.setuid(0)
    try:
        f = open(ofile,"r")
        l = f.readlines()
        f.close()
    except Exception, e:
        ud.debug(ud.LISTENER, ud.ERROR, '4 Failed to open "%s": %s' % (ofile, str(e)) )
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
        ud.debug(ud.LISTENER, ud.ERROR, '4 Failed to write to file "%s": %s' % (wfile, str(e)))
    listener.unsetuid()

# function to delete a textfile with setuid(0) for root-action
def delete_file(fn):
    listener.setuid(0)
    try:
        os.remove(fn)
    except Exception, e:
        ud.debug(ud.LISTENER, ud.ERROR, '4 Failed to remove file "%s": %s' % (fn, str(e)))
    listener.unsetuid()

# function to delete a directory with setuid(0) for root-action
def delete_dir(fn):
    listener.setuid(0)
    try:
        os.rmdir(fn)
    except Exception, e:
        ud.debug(ud.LISTENER, ud.ERROR, '4 Failed to remove file "%s": %s' % (fn, str(e)))
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
        ud.debug(ud.LISTENER, ud.ERROR, '4 Failed to load ip map: %s' % str(e))
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
        ud.debug(ud.LISTENER, ud.ERROR, '4 Failed to write ip map: %s' % str(e))
    listener.unsetuid()

def handler(dn, new, old, command):
    ud.debug(ud.LISTENER, ud.INFO, '4 server2 handler')
    global action
    if command == 'n':
        action = None
        return

    myname = listener.baseConfig['hostname']

    listener.setuid(0)
    lo = ul.getMachineConnection()
    server = lo.search('(cn=' + myname + ')')[0]
    vpnusers = lo.search('(univentionOpenvpnAccount=1)')
    listener.unsetuid()

    key = server[1].get('univentionOpenvpnLicense', [None])[0]
    try:
        l = license(key)
        ud.debug(ud.LISTENER, ud.INFO, '4 Processing license with ID %s:' % l['id'])
        ud.debug(ud.LISTENER, ud.INFO, '4 Valid until: %s' % date.fromordinal(l['vdate']))
        ud.debug(ud.LISTENER, ud.INFO, '4 Users: %s' % l['u'])
        ud.debug(ud.LISTENER, ud.INFO, '4 Site-2-Site: %s' % l['s2s'])
    except:
        pass

    vpnuc = len(vpnusers)
    maxu = maxvpnusers(key)
    ud.debug(ud.LISTENER, ud.INFO, '4 found %u active openvpn users (%u allowed)' % (vpnuc, maxu))
    if vpnuc > maxu:
        action = None
        ud.debug(ud.LISTENER, ud.INFO, '4 skipping actions')
        return			# do nothing

    #### UCS 3 ('Borgfeld') uses openvpn 2.1 - no explicit ip6 support, later version are ok
    relnam =listener.baseConfig.get('version/releasename')
    ip6ok = relnam and relnam != 'Borgfeld'
    if not ip6ok:
        ud.debug(ud.LISTENER, ud.INFO, '4 ipv6 support DISABLED due to version')
    
    port = server[1].get('univentionOpenvpnPort', [None])[0]
    network = server[1].get('univentionOpenvpnNet', [None])[0]
    if not port or not network:
        ud.debug(ud.LISTENER, ud.INFO, '4 missing params, skipping actions')
        action = None
        return			# invalid config, skip
    ipnw = IPNetwork(network)
    if ipnw.size == 1:
        netmask = '255.255.255.0'
        network = str(ipnw.network) + "/24"
    else:
        netmask = str(ipnw.netmask)

    if ip6ok:
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

    if ip6ok:
        if not os.path.exists(fn_ipsv6):
            listener.setuid(0)
            open(fn_ipsv6, 'a').close()
            listener.unsetuid()

    # delete entries on user deletion
    if command == 'd':
        client_cn = old.get('uid', [None])[0]
        delete_file(ccd + client_cn + ".openvpn")
        delete_entry(client_cn, fn_ips)
        if ip6ok:
            delete_entry(client_cn, fn_ipsv6)
        return

    client_cn = new.get('uid', [None])[0]

    # generate and write entries on account activation
    if 'univentionOpenvpnAccount' in new and not 'univentionOpenvpnAccount' in old:
        lines = []

        ip = write_entry(client_cn, fn_ips, network)
        if ip6ok:
            ipv6 = write_entry(client_cn, fn_ipsv6, networkv6)

        lines.append("ifconfig-push " + ip + " " + netmask + "\n")
        if ip6ok:
            lines.append("ifconfig-ipv6-push " + ipv6 + "/" + networkv6.split('/')[1] + "\n")

        write_rc(lines, ccd + client_cn + ".openvpn")

        return

    # delete entries on account deactiviation
    elif not 'univentionOpenvpnAccount' in new and 'univentionOpenvpnAccount' in old:
        delete_file(ccd + client_cn + ".openvpn")
        delete_entry(client_cn, fn_ips)
        if ip6ok: 
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
    ud.debug(ud.LISTENER, ud.INFO, '4 OpenVPN-Server %s' % (action))

    if action == 'stop':
        # deactivate config
        try:
            listener.setuid(0)
            os.rename (fn_serverconf, fn_serverconf + '-disabled')
        except Exception, e:
            listener.unsetuid()
            ud.debug(ud.LISTENER, ud.ERROR, '4 Failed to deactivate server config: %s' % str(e))
            return

    try:
        listener.setuid(0)
        listener.run('/etc/init.d/openvpn', ['openvpn', 'restart', 'server'], uid=0)
    finally:
        listener.unsetuid()

    listener.unsetuid()


### end ###

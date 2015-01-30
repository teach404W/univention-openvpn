#
#       Univention OpenVPN integration -- openvpn-master2.py
#


__package__ = ''  # workaround for PEP 366

import listener
from univention import debug as ud
import univention.uldap as ul

from datetime import date
from M2Crypto import RSA, BIO
from base64 import b64decode


name        = 'openvpn-master2'
description = 'create user openvpn package with updated config'
filter      = '(&(objectClass=univentionOpenvpn)(univentionOpenvpnActive=1))'
attributes  = ['univentionOpenvpnPort', 'univentionOpenvpnAddress']
modrdn      = 1


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
      return None		# invalid license
    vdate = int(items.pop(0))
    if date.today().toordinal() > vdate:
      ud.debug(ud.LISTENER, ud.ERROR, '2 License has expired')
      return None		# expired
    l = {'valid': True}		# at least one feature returned
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
    ud.debug(ud.LISTENER, ud.ERROR, '2 Invalid license')
    return mnlu			# invalid license


# called to create (update) bundle for user when openvpn is activated
def handler(dn, new, old, cmd):
    ud.debug(ud.LISTENER, ud.INFO, '2 master2 handler')

    if cmd == 'n':
        return

    name = new.get('cn', [None])[0]
    port = new.get('univentionOpenvpnPort', [None])[0]
    addr = new.get('univentionOpenvpnAddress', [None])[0]

    if not name or not port or not addr:
        return

    listener.setuid(0)
    lo = ul.getAdminConnection()

    vpnusers = lo.search('(univentionOpenvpnAccount=1)')
    vpnuc = len(vpnusers)

    maxu = maxvpnusers(new.get('univentionOpenvpnLicense', [None])[0])

    ud.debug(ud.LISTENER, ud.INFO, '2 found %u active openvpn users (%u allowed)' % (vpnuc, maxu))

    if vpnuc > maxu:
        listener.unsetuid()
        ud.debug(ud.LISTENER, ud.INFO, '2 skipping actions')
        return			# do nothing

    for user in vpnusers:
        uid = user[1].get('uid', [None])[0]
        home = user[1].get('homeDirectory', ['/dev/null'])[0]
        ud.debug(ud.LISTENER, ud.INFO, '2 create new certificate for %s in %s' % (uid, home))

        if uid and home:
        # update bundle for this openvpn server with new config
            try:
                listener.run('/usr/lib/openvpn-int/create-bundle', ['create-bundle', 'no', uid, home, name, addr, port], uid=0)
            finally:
                listener.unsetuid()

    listener.unsetuid()


### end ###

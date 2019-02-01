
import os as _os
import json as _json

from cachetools import cached as _cached
from cachetools import LRUCache as _LRUCache

from Acquire.ObjectStore import ObjectStore as _ObjectStore
from Acquire.ObjectStore import string_to_bytes as _string_to_bytes
from Acquire.ObjectStore import bytes_to_string as _bytes_to_string
from Acquire.ObjectStore import url_to_encoded as _url_to_encoded
from Acquire.ObjectStore import Mutex as _Mutex
from Acquire.ObjectStore import get_datetime_now_to_string as \
                                _get_datetime_now_to_string

from ._service import Service as _Service
from ._login_to_objstore import get_service_account_bucket \
                            as _get_service_account_bucket

from ._errors import ServiceError, ServiceAccountError, \
                     MissingServiceAccountError

# The cache can hold a maximum of 5 objects, and will replace the least
# recently used items first
_cache_serviceinfo_data = _LRUCache(maxsize=5)
_cache_service_info = _LRUCache(maxsize=5)
_cache_adminusers = _LRUCache(maxsize=5)
_cache_serviceuser = _LRUCache(maxsize=5)
_cache_service_account_uid = _LRUCache(maxsize=5)


__all__ = ["setup_service_info", "add_admin_user",
           "get_service_info", "get_admin_users",
           "get_service_private_key",
           "get_service_private_certificate", "get_service_public_key",
           "get_service_public_certificate",
           "clear_serviceinfo_cache",
           "get_service_user_account_uid", "create_service_user_account"]


# The key in the object store for the service object
_service_key = "_service_key"


def clear_serviceinfo_cache():
    """Clear the caches used to accelerate loading the service info
       and admin user objects
    """
    _cache_adminusers.clear()
    _cache_service_info.clear()
    _cache_serviceinfo_data.clear()
    _cache_serviceuser.clear()
    _cache_service_account_uid.clear()


# Cache this function as the data will rarely change, and this
# will prevent too many runs to the ObjectStore
@_cached(_cache_serviceinfo_data)
def _get_service_info_data():
    """Internal function that loads up the service info data from
       the object store.
    """

    # get the bucket again - can't pass as an argument as this is a cached
    # function - luckily _get_service_account_bucket is also a cached function
    try:
        bucket = _get_service_account_bucket()
    except ServiceAccountError as e:
        raise e
    except Exception as e:
        raise ServiceAccountError(
            "Cannot log into the service account: %s" % str(e))

    # find the service info from the object store
    try:
        service = _ObjectStore.get_object_from_json(bucket, _service_key)
    except Exception as e:
        raise MissingServiceAccountError(
            "Unable to load the service account for this service. An "
            "error occured while loading the data from the object "
            "store: %s" % str(e))

    if not service:
        raise MissingServiceAccountError(
            "You haven't yet created the service account "
            "for this service. Please create an account first.")

    return service


def _get_service_password():
    """Function used to get the primary password that locks the
       skeleton key that secures the entire tree of trust from the
       Service object
    """
    service_password = _os.getenv("SERVICE_PASSWORD")

    if service_password is None:
        raise ServiceAccountError(
            "You must supply a $SERVICE_PASSWORD")

    return service_password


def setup_service_info(canonical_url, service_type):
    """Call this function to setup a new
       service that will serve at 'canonical_url', will be of
       the specified service_type.

        (1) Delete the object store value "_service" if you want to reset
            the actual Service. This will assign a new UID for the service
            which would reset the certificates and keys. This new service
            will need to be re-introduced to other services that need
            to trust it
    """
    bucket = _get_service_account_bucket()

    from Acquire.ObjectStore import Mutex as _Mutex

    # ensure that this is the only time the service is set up
    mutex = _Mutex(key=_service_key, bucket=bucket)

    try:
        service_info = _ObjectStore.get_object_from_json(bucket, _service_key)
    except:
        service_info = None

    service = None
    service_password = _get_service_password()

    if service_info:
        try:
            service = _Service.from_data(service_info, service_password)
        except Exception as e:
            raise ServiceAccountError(
                "Something went write reading the Service data. You should "
                "either debug the error or delete the data at key '%s' "
                "to allow the service to be reset and constructed again. "
                "The error was %s"
                % (_service_key, str(e)))

        if service.service_type() != service_type or \
           service.canonical_url() != canonical_url:
            raise ServiceAccountError(
               "The existing service has a different type or URL to that "
               "requested at setup. The request type and URL are %s and %s, "
               "while the actual service type and URL are %s and %s." %
               (service_type, canonical_url,
                service.service_type(), service.canonical_url()))

    if service is None:
        if (service_type is None) or (canonical_url is None):
            raise ServiceAccountError(
                "You need to supply both the service_type and canonical_url "
                "in order to initialise a new Service")

        # we need to build the service account
        service = _Service(service_url=canonical_url,
                           service_type=service_type)

        # now create the service user account

        # write the service data, encrypted using the service password
        service_data = service.to_data(service_password)
        # reload the data to check it is ok, and also to set the right class
        service = _Service.from_data(service_data, service_password)
        # now it is ok, save this data to the object store
        _ObjectStore.set_object_from_json(bucket, _service_key, service_data)

    mutex.unlock()

    return service


def add_admin_user(service, account_uid, authorisation=None):
    """Function that is called to add a new user account as a service
       administrator. If this is the first account then authorisation
       is not needed. If this is the second or subsequent admin account,
       then you need to provide an authorisation signed by one of the
       existing admin users. If you need to reset the admin users then
       delete the user accounts from the service.
    """
    bucket = _get_service_account_bucket()

    from Acquire.ObjectStore import Mutex as _Mutex

    # see if the admin account details exists...
    admin_key = "%s/admin_users" % _service_key

    # ensure that we have exclusive access to this service
    mutex = _Mutex(key=admin_key, bucket=bucket)

    try:
        admin_users = _ObjectStore.get_object_from_json(bucket, admin_key)
    except:
        admin_users = None

    if admin_users is None:
        # this is the first admin user - automatically accept
        admin_users = {}
        authorised_by = "first admin"
    else:
        # validate that the new user has been authorised by an existing
        # admin...
        if authorisation is None:
            raise ServiceAccountError(
                "You must supply a valid authorisation from an existing admin "
                "user if you want to add a new admin user.")

        if authorisation.user_uid() not in admin_users:
            raise ServiceAccountError(
                "The authorisation for the new admin account is not valid "
                "because the user who signed it is not a valid admin on "
                "this service.")

        authorisation.verify(account_uid)
        authorised_by = authorisation.user_uid()

    # everything is ok - add this admin user to the admin_users
    # dictionary - save the date and time they became an admin,
    # and how they achieved this status (first admin, or whoever
    # authorised them)
    admin_users[account_uid] = {"datetime": _get_datetime_now_to_string(),
                                "authorised_by": authorised_by}

    # everything is done, so now write this data to the object store
    _ObjectStore.set_object_from_json(bucket, admin_key,
                                      _json.dumps(admin_users))

    # we can (finally!) release the mutex, as everyone else should now
    # be able to see the account
    mutex.unlock()

    _cache_adminusers.clear()


@_cached(_cache_adminusers)
def get_admin_users():
    """This function returns all of the admin_users data. This is a
       dictionary of the UIDs of all of the admin users
    """
    try:
        bucket = _get_service_account_bucket()
    except ServiceAccountError as e:
        raise e
    except Exception as e:
        raise ServiceAccountError(
            "Cannot log into the service account: %s" % str(e))

    # find the admin accounts info from the object store
    try:
        key = "%s/admin_users" % _service_key
        admin_users = _ObjectStore.get_object_from_json(bucket, key)
    except Exception as e:
        raise MissingServiceAccountError(
            "Unable to load the Admin User data for this service. An "
            "error occured while loading the data from the object "
            "store: %s" % str(e))

    if not admin_users:
        raise MissingServiceAccountError(
            "You haven't yet created any Admin Users for the service account "
            "for this service. Please create an Admin User first.")

    return admin_users


@_cached(_cache_service_info)
def get_service_info(need_private_access=False):
    """Return the service info object for this service. If private
       access is needed then this will decrypt and access the private
       keys and signing certificates, which is slow if you just need
       the public certificates.
    """
    try:
        service_info = _get_service_info_data()
    except Exception as e:
        raise MissingServiceAccountError(
            "Unable to read the service info from the object store! : %s" %
            str(e))

    service_password = None

    if need_private_access:
        service_password = _get_service_password()

    try:
        if service_password:
            service = _Service.from_data(service_info, service_password)
        else:
            service = _Service.from_data(service_info)

    except Exception as e:
        raise MissingServiceAccountError(
            "Unable to create the ServiceAccount object: %s" % str(e))

    return service


@_cached(_cache_service_account_uid)
def get_service_user_account_uid(accounting_service_uid):
    """Return the UID of the financial Acquire.Accounting.Account
       that is held on the accounting service with UID
       'accounting_service_uid' for the service user on this
       service. This is the account to which payment for this
       service should be sent
    """
    bucket = _get_service_account_bucket()

    key = "%s/account/%s" % (_service_key, accounting_service_uid)

    try:
        account_uid = _ObjectStore.get_string_object(bucket, key)
    except:
        account_uid = None

    if account_uid is None:
        raise ServiceAccountError(
            "This service does not have a valid financial account on "
            "the accounting service at '%s'" % accounting_service_uid)

    return account_uid


def create_service_user_account(service, accounting_service_url):
    """Call this function to create the financial service account
       for this service on the accounting service at 'accounting_service_url'

       This does nothing if the account already exists
    """
    accounting_service = service.get_trusted_service(accounting_service_url)
    accounting_service_uid = accounting_service.uid()

    key = "%s/account/%s" % (_service_key, accounting_service_uid)
    bucket = service.bucket()

    try:
        account_uid = _ObjectStore.get_string_object(bucket, key)
    except:
        account_uid = None

    if account_uid:
        # we already have an account...
        return

    service_user = service.login_service_user()

    from Acquire.Client import create_account as _create_account

    account = _create_account(
                service_user, "main",
                "Main account to receive payment for all use on service "
                "%s (%s)" % (service.canonical_url(), service.uid()),
                accounting_service=accounting_service)

    account_uid = account.uid()

    _ObjectStore.set_string_object(bucket, key, account_uid)


def _refresh_service_keys_and_certs(service):
    """This function will check if any key rotation is needed, and
       if so, it will automatically refresh the keys and certificates.
       The old keys and certificates will be stored in a database of
       old keys and certificates
    """
    if not service.should_refresh_keys():
        return service

    # save the old keys
    oldkeys = service.dump_keys()

    # generate new keys
    last_update = service.last_key_update()
    service.refresh_keys()

    # now lock the object store so that we are the only function
    # that can write the new keys to global state
    bucket = _get_service_account_bucket()
    m = _Mutex(key=service.uid(), bucket=bucket)

    service_data = _ObjectStore.get_object_from_json(bucket, _service_key)
    service_info = _Service.from_data(service_data)

    if service_info.last_key_update() == last_update:
        # no-one else has beaten us - write the updated keys to global state
        _ObjectStore.set_object_from_json(bucket, _service_key,
                                          service.to_data(
                                              _get_service_password()))
        m.unlock()

        # now write the old keys to storage
        key = "%s/oldkeys/%s" % (_service_key, oldkeys["datetime"])
        _ObjectStore.set_object_from_json(bucket, key, oldkeys)
    else:
        m.unlock()

    # clear the cache as we will need to load a new object
    clear_serviceinfo_cache()

    return get_service_info(need_private_access=True)


def get_service_private_key(fingerprint=None):
    """This function returns the private key for this service"""
    s = get_service_info(need_private_access=True)
    s = _refresh_service_keys_and_certs(s)
    key = s.private_key()

    if fingerprint:
        if key.fingerprint() != fingerprint:
            key = s.last_key()

        if key.fingerprint() != fingerprint:
            raise ServiceAccountError(
                "Cannot find a private key for '%s' that matches "
                "the fingerprint %s" % (str(s), fingerprint))

    return key


def get_service_private_certificate(fingerprint=None):
    """This function returns the private signing certificate
       for this service
    """
    s = get_service_info(need_private_access=True)
    s = _refresh_service_keys_and_certs(s)
    cert = s.private_certificate()

    if fingerprint:
        if cert.fingerprint() != fingerprint:
            raise ServiceAccountError(
                "Cannot find a private certificate for '%s' that matches "
                "the fingerprint %s" % (str(s), fingerprint))

    return cert


def get_service_public_key(fingerprint=None):
    """This function returns the public key for this service"""
    s = get_service_info(need_private_access=False)
    key = s.public_key()

    if fingerprint:
        if key.fingerprint() != fingerprint:
            raise ServiceAccountError(
                "Cannot find a public key for '%s' that matches "
                "the fingerprint %s" % (str(s), fingerprint))

    return key


def get_service_public_certificate(fingerprint=None):
    """This function returns the public certificate for this service"""
    s = get_service_info(need_private_access=False)
    cert = s.public_certificate()

    if fingerprint:
        if cert.fingerprint() != fingerprint:
            cert = s.last_certificate()

        if cert.fingerprint() != fingerprint:
            raise ServiceAccountError(
                "Cannot find a public certificate for '%s' that matches "
                "the fingerprint %s" % (str(s), fingerprint))

    return cert

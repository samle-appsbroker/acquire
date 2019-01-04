
import io as _io
import datetime as _datetime
import uuid as _uuid
import json as _json
import os as _os
import copy as _copy
import uuid as _uuid

from ._par import PAR as _PAR

from ._errors import ObjectStoreError, PARError, PARPermissionsError

__all__ = ["OCI_ObjectStore"]


def _sanitise_bucket_name(bucket_name):
    """This function sanitises the passed bucket name. It will always
        return a valid bucket name. If "None" is passed, then a new,
        unique bucket name will be generated"""

    if bucket_name is None:
        return str(_uuid.uuid4())

    return "_".join(bucket_name.split())


def _get_object_url_for_region(region, uri):
    """Internal function used to get the full URL to the passed PAR URI
       for the specified region. This has the format;

       https://objectstorage.{region}.oraclecloud.com/{uri}
    """
    server = "https://objectstorage.%s.oraclecloud.com" % region

    while uri.startswith("/"):
        uri = uri[1:]

    return "%s/%s" % (server,uri)


class OCI_ObjectStore:
    """This is the backend that abstracts using the Oracle Cloud
       Infrastructure object store
    """

    @staticmethod
    def create_bucket(bucket, bucket_name, compartment=None):
        """Create and return a new bucket in the object store called
           'bucket_name', optionally placing it into the compartment
           identified by 'compartment'. This will raise an
           ObjectStoreError if this bucket already exists
        """
        new_bucket = _copy.copy(bucket)

        new_bucket["bucket_name"] = str(bucket_name)

        if compartment is not None:
            new_bucket["compartment_id"] = str(compartment)

        try:
            from oci.object_storage.models import CreateBucketDetails as \
                _CreateBucketDetails
        except:
            raise ImportError(
                "Cannot import OCI. Please install OCI, e.g. via "
                "'pip install oci' so that you can connect to the "
                "Oracle Cloud Infrastructure")

        try:
            request = _CreateBucketDetails()
            request.compartment_id = new_bucket["compartment_id"]
            client = new_bucket["client"]
            request.name = _sanitise_bucket_name(bucket_name)

            new_bucket["bucket"] = client.create_bucket(
                                        client.get_namespace().data,
                                        request).data
        except Exception as e:
            # couldn't create the bucket - likely because it already
            # exists - try to connect to the existing bucket
            raise ObjectStoreError(
                "Unable to create the bucket '%s', likely because it "
                "already exists: %s" % (bucket_name, str(e)))

        return new_bucket

    @staticmethod
    def get_bucket(bucket, bucket_name, compartment=None,
                   create_if_needed=True):
        """Find and return a new bucket in the object store called
           'bucket_name', optionally placing it into the compartment
           identified by 'compartment'. If 'create_if_needed' is True
           then the bucket will be created if it doesn't exist. Otherwise,
           if the bucket does not exist then an exception will be raised.
        """
        new_bucket = _copy.copy(bucket)

        new_bucket["bucket_name"] = str(bucket_name)

        if compartment is not None:
            new_bucket["compartment_id"] = str(compartment)

        try:
            from oci.object_storage.models import CreateBucketDetails as \
                _CreateBucketDetails
        except:
            raise ImportError(
                "Cannot import OCI. Please install OCI, e.g. via "
                "'pip install oci' so that you can connect to the "
                "Oracle Cloud Infrastructure")

        # try to get the existing bucket
        client = new_bucket["client"]
        namespace = client.get_namespace().data
        sanitised_name = _sanitise_bucket_name(str(bucket_name))

        try:
            existing_bucket = client.get_bucket(
                                namespace, sanitised_name).data
        except:
            existing_bucket = None

        if existing_bucket:
            new_bucket["bucket"] = existing_bucket
            return new_bucket

        if create_if_needed:
            try:
                request = _CreateBucketDetails()
                request.compartment_id = new_bucket["compartment_id"]
                request.name = sanitised_name

                client.create_bucket(namespace, request)
            except:
                pass

            try:
                existing_bucket = client.get_bucket(
                                    namespace, sanitised_name).data
            except:
                existing_bucket = None

        if existing_bucket is None:
            raise ObjectStoreError(
                "There is not bucket called '%s'. Please check the "
                "compartment and access permissions." % bucket_name)

        new_bucket["bucket"] = existing_bucket

        return new_bucket

    @staticmethod
    def create_par(bucket, key=None, readable=True,
                   writeable=False, duration=3600):
        """Create a pre-authenticated request for the passed bucket and
           key (if key is None then the request is for the entire bucket).
           This will return a PAR object that will contain a URL that can
           be used to access the object/bucket. If writeable is true, then
           the URL will also allow the object/bucket to be written to.
           PARs are time-limited. Set the lifetime in seconds by passing
           in 'duration' (by default this is one hour)
        """

        # get the UTC timestamp when this PAR should expire
        expires_datetime = _datetime.datetime.utcnow() + \
            _datetime.timedelta(seconds=duration)

        expires_timestamp = expires_datetime.replace(
            tzinfo=_datetime.timezone.utc).timestamp()

        is_bucket = (key is None)

        # Limitation of OCI - cannot have a bucket PAR with
        # read permissions!
        if is_bucket and readable:
            raise PARError(
                "You cannot create a Bucket PAR that has read permissions "
                "due to a limitation in the underlying platform")

        try:
            from oci.object_storage.models import \
                CreatePreauthenticatedRequestDetails as \
                _CreatePreauthenticatedRequestDetails
        except:
            raise ImportError(
                "Cannot import OCI. Please install OCI, e.g. via "
                "'pip install oci' so that you can connect to the "
                "Oracle Cloud Infrastructure")

        oci_par = None

        try:
            request = _CreatePreauthenticatedRequestDetails()

            if is_bucket:
                request.access_type = "AnyObjectWrite"
            elif readable and writeable:
                request.access_type = "ObjectReadWrite"
            elif readable:
                request.access_type = "ObjectRead"
            elif writeable:
                request.access_type = "ObjectWrite"
            else:
                raise ObjectStoreError(
                    "Unsupported permissions model for PAR!")

            request.name = str(_uuid.uuid4())

            if not is_bucket:
                request.object_name = key

            request.time_expires = expires_datetime

            client = bucket["client"]

            oci_par = client.create_preauthenticated_request(
                                        client.get_namespace().data,
                                        bucket["bucket_name"],
                                        request).data
        except Exception as e:
            # couldn't create the preauthenticated request
            raise ObjectStoreError(
                "Unable to create the preauthenticated request '%s': %s" %
                (str(request), str(e)))

        if oci_par is None:
            raise ObjectStoreError(
                "Unable to create the preauthenticated request!")

        created_timestamp = oci_par.time_created.replace(
                                tzinfo=_datetime.timezone.utc).timestamp()

        expires_timestamp = oci_par.time_expires.replace(
                                tzinfo=_datetime.timezone.utc).timestamp()

        # the URI returned by OCI does not include the server. We need
        # to get the server based on the region of this bucket
        url = _get_object_url_for_region(bucket["region"],
                                         oci_par.access_uri)

        par = _PAR(url=url, key=oci_par.object_name,
                   created_timestamp=created_timestamp,
                   expires_timestamp=expires_timestamp,
                   is_readable=readable,
                   is_writeable=writeable,
                   par_id=str(oci_par.id),
                   par_name=str(oci_par.name),
                   driver="oci")

        return par

    @staticmethod
    def get_object_as_file(bucket, key, filename):
        """Get the object contained in the key 'key' in the passed 'bucket'
           and writing this to the file called 'filename'"""

        try:
            response = bucket["client"].get_object(bucket["namespace"],
                                                   bucket["bucket_name"],
                                                   key)
            is_chunked = False
        except:
            try:
                response = bucket["client"].get_object(bucket["namespace"],
                                                       bucket["bucket_name"],
                                                       "%s/1" % key)
                is_chunked = True
            except:
                is_chunked = False
                pass

            if not is_chunked:
                raise ObjectStoreError("No object at key '%s'" % key)

        if not is_chunked:
            with open(filename, 'wb') as f:
                for chunk in response.data.raw.stream(1024 * 1024,
                                                      decode_content=False):
                    f.write(chunk)

            return filename

        # the data is chunked - get this out chunk by chunk
        with open(filename, 'wb') as f:
            next_chunk = 1
            while True:
                for chunk in response.data.raw.stream(1024 * 1024,
                                                      decode_content=False):
                    f.write(chunk)

                # now get and write the rest of the chunks
                next_chunk += 1

                try:
                    response = bucket["client"].get_object(
                        bucket["namespace"], bucket["bucket_name"],
                        "%s/%d" % (key, next_chunk))
                except:
                    break

    @staticmethod
    def get_object(bucket, key):
        """Return the binary data contained in the key 'key' in the
           passed bucket"""

        try:
            response = bucket["client"].get_object(bucket["namespace"],
                                                   bucket["bucket_name"],
                                                   key)
            is_chunked = False
        except:
            try:
                response = bucket["client"].get_object(bucket["namespace"],
                                                       bucket["bucket_name"],
                                                       "%s/1" % key)
                is_chunked = True
            except:
                is_chunked = False
                pass

            # Raise the original error
            if not is_chunked:
                raise ObjectStoreError("No data at key '%s'" % key)

        data = None

        for chunk in response.data.raw.stream(1024 * 1024,
                                              decode_content=False):
            if not data:
                data = chunk
            else:
                data += chunk

        if is_chunked:
            # keep going through to find more chunks
            next_chunk = 1

            while True:
                next_chunk += 1

                try:
                    response = bucket["client"].get_object(
                                        bucket["namespace"],
                                        bucket["bucket_name"],
                                        "%s/%d" % (key, next_chunk))
                except:
                    response = None
                    break

                for chunk in response.data.raw.stream(1024 * 1024,
                                                      decode_content=False):
                    if not data:
                        data = chunk
                    else:
                        data += chunk

        return data

    @staticmethod
    def get_string_object(bucket, key):
        """Return the string in 'bucket' associated with 'key'"""
        return OCI_ObjectStore.get_object(bucket, key).decode("utf-8")

    @staticmethod
    def get_object_from_json(bucket, key):
        """Return an object constructed from json stored at 'key' in
           the passed bucket. This returns None if there is no data
           at this key
        """
        data = None

        try:
            data = OCI_ObjectStore.get_string_object(bucket, key)
        except:
            return None

        return _json.loads(data)

    @staticmethod
    def get_all_object_names(bucket, prefix=None):
        """Returns the names of all objects in the passed bucket"""
        objects = bucket["client"].list_objects(bucket["namespace"],
                                                bucket["bucket_name"],
                                                prefix=prefix).data

        names = []

        for obj in objects.objects:
            if prefix:
                if obj.name.startswith(prefix):
                    names.append(obj.name[len(prefix)+1:])
            else:
                names.append(obj.name)

        return names

    @staticmethod
    def get_all_objects(bucket, prefix=None):
        """Return all of the objects in the passed bucket"""
        objects = {}
        names = OCI_ObjectStore.get_all_object_names(bucket, prefix)

        if prefix:
            for name in names:
                objects[name] = OCI_ObjectStore.get_object(
                                        bucket, "%s/%s" % (prefix, name))
        else:
            for name in names:
                objects[name] = OCI_ObjectStore.get_object(bucket, name)

        return objects

    @staticmethod
    def get_all_strings(bucket, prefix=None):
        """Return all of the strings in the passed bucket"""
        objects = OCI_ObjectStore.get_all_objects(bucket, prefix)

        names = list(objects.keys())

        for name in names:
            try:
                s = objects[name].decode("utf-8")
                objects[name] = s
            except:
                del objects[name]

        return objects

    @staticmethod
    def set_object(bucket, key, data):
        """Set the value of 'key' in 'bucket' to binary 'data'"""
        f = _io.BytesIO(data)

        bucket["client"].put_object(bucket["namespace"],
                                    bucket["bucket_name"],
                                    key, f)

    @staticmethod
    def set_object_from_file(bucket, key, filename):
        """Set the value of 'key' in 'bucket' to equal the contents
           of the file located by 'filename'"""

        with open(filename, 'rb') as f:
            bucket["client"].put_object(bucket["namespace"],
                                        bucket["bucket_name"],
                                        key, f)

    @staticmethod
    def set_string_object(bucket, key, string_data):
        """Set the value of 'key' in 'bucket' to the string 'string_data'"""
        OCI_ObjectStore.set_object(bucket, key, string_data.encode("utf-8"))

    @staticmethod
    def set_object_from_json(bucket, key, data):
        """Set the value of 'key' in 'bucket' to equal to contents
           of 'data', which has been encoded to json"""
        OCI_ObjectStore.set_string_object(bucket, key, _json.dumps(data))

    @staticmethod
    def log(bucket, message, prefix="log"):
        """Log the the passed message to the object store in
           the bucket with key "key/timestamp" (defaults
           to "log/timestamp"
        """
        OCI_ObjectStore.set_string_object(
                bucket, "%s/%s" % (prefix,
                                   _datetime.datetime.utcnow().timestamp()),
                str(message))

    @staticmethod
    def delete_all_objects(bucket, prefix=None):
        """Deletes all objects..."""

        if prefix:
            for obj in OCI_ObjectStore.get_all_object_names(bucket, prefix):
                if len(obj) == 0:
                    bucket["client"].delete_object(bucket["namespace"],
                                                   bucket["bucket_name"],
                                                   prefix)
                else:
                    bucket["client"].delete_object(bucket["namespace"],
                                                   bucket["bucket_name"],
                                                   "%s/%s" % (prefix, obj))
        else:
            for obj in OCI_ObjectStore.get_all_object_names(bucket):
                bucket["client"].delete_object(bucket["namespace"],
                                               bucket["bucket_name"],
                                               obj)

    @staticmethod
    def get_log(bucket, log="log"):
        """Return the complete log as an xml string"""
        objs = OCI_ObjectStore.get_all_strings(bucket, log)

        lines = []
        lines.append("<log>")

        timestamps = list(objs.keys())
        timestamps.sort()

        for timestamp in timestamps:
            lines.append("<logitem>")
            lines.append("<timestamp>%s</timestamp>" %
                         _datetime.datetime.fromtimestamp(float(timestamp)))
            lines.append("<message>%s</message>" % objs[timestamp])
            lines.append("</logitem>")

        lines.append("</log>")

        return "".join(lines)

    @staticmethod
    def clear_log(bucket, log="log"):
        """Clears out the log"""
        OCI_ObjectStore.delete_all_objects(bucket, log)

    @staticmethod
    def delete_object(bucket, key):
        """Removes the object at 'key'"""
        try:
            bucket["client"].delete_object(bucket["namespace"],
                                           bucket["bucket_name"],
                                           key)
        except:
            pass

    @staticmethod
    def clear_all_except(bucket, keys):
        """Removes all objects from the passed 'bucket' except those
           whose keys are or start with any key in 'keys'"""
        names = OCI_ObjectStore.get_all_object_names(bucket)

        for name in names:
            remove = True

            for key in keys:
                if name.startswith(key):
                    remove = False
                    break

            if remove:
                bucket["client"].delete_object(bucket["namespace"],
                                               bucket["bucket_name"],
                                               name)

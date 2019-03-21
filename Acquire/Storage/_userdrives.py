
__all__ = ["UserDrives"]

_drives_root = "storage/drives"

_subdrives_root = "storage/subdrives"


class UserDrives:
    """This class holds all of the information about all of
       the drives that a specific user can access. This holds
       the relationships between those user drives, e.g.
       drives can be nested in other drives.
    """
    def __init__(self, authorisation=None, user_guid=None):
        """Construct either from a user-authorisation or specifying
           the user's GUID directly
        """
        if authorisation is not None:
            from Acquire.Identity import Authorisation as _Authorisation
            if not isinstance(authorisation, _Authorisation):
                raise TypeError(
                    "You can only authorise UserDrives with a valid "
                    "Authorisation object")

            authorisation.verify(resource="UserDrives")

            self._is_authorised = True

            if user_guid is not None:
                if authorisation.user_guid() != user_guid:
                    raise PermissionError(
                        "Disagreement of user_guid: %s versus %s" %
                        (authorisation.user_guid(), user_guid))

            self._user_guid = authorisation.user_guid()
        else:
            self._user_guid = str(user_guid)
            self._is_authorised = False

    def is_null(self):
        """Return whether or not this is null"""
        return self._user_guid is None

    def list_drives(self):
        """Return a list of all of the top-level drives to which this user
           has access. This returned names are the user-assigned
           names of the drives, not their uids
        """
        if self.is_null():
            return []

        from Acquire.Service import get_service_account_bucket \
            as _get_service_account_bucket
        from Acquire.ObjectStore import ObjectStore as _ObjectStore
        from Acquire.ObjectStore import encoded_to_string as _encoded_to_string

        bucket = _get_service_account_bucket()

        names = _ObjectStore.get_all_object_names(
                    bucket, "%s/%s" % (_drives_root, self._user_guid))

        drives = []
        for name in names:
            drives.append(_encoded_to_string(name.split("/")[-1]))

        return drives

    def _get_subdrive(self, drive_uid, name, autocreate=True):
        """Return the DriveInfo for the Drive that the user has
           called 'name' in the drive with UID 'drive_uid'. If
           'autocreate' is True then this drive is automatically
           created if it does not exist.
        """
        if self.is_null():
            raise PermissionError(
                "You cannot get a DriveInfo from a null UserDrives")

        from Acquire.ObjectStore import string_to_filepath_parts \
            as _string_to_filepath_parts

        parts = _string_to_filepath_parts(name)

        if len(parts) != 1:
            raise ValueError(
                "The passed drive name '%s' is not valid!" % name)

        from Acquire.Service import get_service_account_bucket \
            as _get_service_account_bucket
        from Acquire.ObjectStore import ObjectStore as _ObjectStore

        from Acquire.ObjectStore import string_to_encoded as _string_to_encoded

        encoded_name = _string_to_encoded(name)

        bucket = _get_service_account_bucket()

        drive_key = "%s/%s/%s/%s" % (_subdrives_root, self._user_guid,
                                     drive_uid, encoded_name)

        try:
            drive_uid = _ObjectStore.get_string_object(
                                                bucket, drive_key)
        except:
            drive_uid = None

        if drive_uid is not None:
            from Acquire.Storage import DriveInfo as _DriveInfo
            drive = _DriveInfo(drive_uid=drive_uid, user_guid=self._user_guid,
                               is_authorised=self._is_authorised)
        else:
            drive = None

        if drive is None:
            if self._is_authorised and autocreate:
                # create a new UID for the drive and write this to the
                # object store
                from Acquire.ObjectStore import create_uuid as _create_uuid

                drive_uid = _create_uuid()

                drive_uid = _ObjectStore.set_ins_string_object(
                                            bucket, drive_key, drive_uid)

                from Acquire.Storage import DriveInfo as _DriveInfo
                drive = _DriveInfo(drive_uid=drive_uid,
                                   user_guid=self._user_guid,
                                   is_authorised=self._is_authorised)

        return drive

    def get_drive(self, name, autocreate=True):
        """Return the DriveInfo for the Drive that the user has
           called 'name'. If 'autocreate' is True then this
           drive is automatically created if it does not exist. Note
           that the '/' in the name will be interpreted as drive
           separators.
        """
        if self.is_null():
            raise PermissionError(
                "You cannot get a DriveInfo from a null UserDrives")

        # break the name into directory parts
        from Acquire.ObjectStore import string_to_filepath_parts \
            as _string_to_filepath_parts

        parts = _string_to_filepath_parts(name)

        # first get the root drive...
        root_name = parts[0]

        from Acquire.Service import get_service_account_bucket \
            as _get_service_account_bucket
        from Acquire.ObjectStore import ObjectStore as _ObjectStore

        from Acquire.ObjectStore import string_to_encoded as _string_to_encoded

        encoded_name = _string_to_encoded(root_name)

        bucket = _get_service_account_bucket()

        drive_key = "%s/%s/%s" % (_drives_root, self._user_guid,
                                  encoded_name)

        try:
            drive_uid = _ObjectStore.get_string_object(
                                                bucket, drive_key)
        except:
            drive_uid = None

        if drive_uid is not None:
            from Acquire.Storage import DriveInfo as _DriveInfo
            drive = _DriveInfo(drive_uid=drive_uid, user_guid=self._user_guid,
                               is_authorised=self._is_authorised)
        else:
            drive = None

        if drive is None:
            if self._is_authorised and autocreate:
                # create a new UID for the drive and write this to the
                # object store
                from Acquire.ObjectStore import create_uuid as _create_uuid

                drive_uid = _create_uuid()

                drive_uid = _ObjectStore.set_ins_string_object(
                                            bucket, drive_key, drive_uid)

                from Acquire.Storage import DriveInfo as _DriveInfo
                drive = _DriveInfo(drive_uid=drive_uid,
                                   user_guid=self._user_guid,
                                   is_authorised=self._is_authorised)

        if drive is None:
            from Acquire.Storage import MissingDriveError
            raise MissingDriveError(
                "There is no Drive called '%s' available" % name)

        # now we have the drive, get the sub-drive in this drive...
        if len(parts) > 1:
            for subdrive in parts[1:]:
                drive = self._get_subdrive(
                                    drive_uid=drive.uid(),
                                    name=subdrive,
                                    autocreate=autocreate)

                if drive is None:
                    from Acquire.Storage import MissingDriveError
                    raise MissingDriveError(
                        "There is no Drive called '%s' available" % name)

        return drive

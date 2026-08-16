"""Pet identity: the reference face photos the device's own NPU matches against.

Recognition does NOT happen here. On the camera litter models the NPU does the
matching on-device; this package only keeps the pet records and hosts their
reference photos where `dev_discern_pic` can point the device at them. The
result comes back as a `petId` in an event's content - and because that id is
this package's own primary key, handed to the device by us, there is no
id-mapping protocol to implement.
"""

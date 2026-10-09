Type stubs for the hardware libraries TrafficPi uses. Picamera2, libcamera and HailoRT are apt
packages without type information (and are not installable on a development laptop or in CI), so
these stubs describe just the parts the code calls. Extend them when new calls are added.

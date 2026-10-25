#!/data/data/com.termux/files/usr/bin/python
"""Read Vulkan driver identity without allocating a model or a GPU workload."""
import ctypes as C
import json

class ApplicationInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('name', C.c_char_p),
        ('version', C.c_uint32), ('engine', C.c_char_p), ('engineVersion', C.c_uint32), ('apiVersion', C.c_uint32)]

class InstanceInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32),
        ('application', C.POINTER(ApplicationInfo)), ('layerCount', C.c_uint32), ('layers', C.c_void_p),
        ('extensionCount', C.c_uint32), ('extensions', C.c_void_p)]

class DriverProperties(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('driverID', C.c_uint32),
        ('name', C.c_char * 256), ('info', C.c_char * 256), ('conformance', C.c_ubyte * 4)]

class Properties2(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('properties', C.c_uint32 * 1024)]

vk = C.CDLL('libvulkan.so')
vk.vkCreateInstance.argtypes = [C.POINTER(InstanceInfo), C.c_void_p, C.POINTER(C.c_void_p)]
vk.vkEnumeratePhysicalDevices.argtypes = [C.c_void_p, C.POINTER(C.c_uint32), C.POINTER(C.c_void_p)]
vk.vkGetPhysicalDeviceProperties2.argtypes = [C.c_void_p, C.POINTER(Properties2)]
vk.vkDestroyInstance.argtypes = [C.c_void_p, C.c_void_p]
application = ApplicationInfo(sType=0, name=b'driver-diagnostic', apiVersion=(1 << 22) | (2 << 12))
info = InstanceInfo(sType=1, application=C.pointer(application))
instance = C.c_void_p()
assert vk.vkCreateInstance(C.byref(info), None, C.byref(instance)) == 0
try:
    count = C.c_uint32()
    assert vk.vkEnumeratePhysicalDevices(instance, C.byref(count), None) == 0
    devices = (C.c_void_p * count.value)()
    assert vk.vkEnumeratePhysicalDevices(instance, C.byref(count), devices) == 0
    results = []
    for device in devices:
        driver = DriverProperties(sType=1000196000)
        props = Properties2(sType=1000059001, pNext=C.cast(C.pointer(driver), C.c_void_p))
        vk.vkGetPhysicalDeviceProperties2(device, C.byref(props))
        name = C.string_at(C.addressof(props.properties) + 20, 256).split(b'\0')[0].decode()
        api = props.properties[0]
        results.append({'device': name, 'driverName': driver.name.decode(), 'driverInfo': driver.info.decode(),
            'driverID': driver.driverID, 'driverVersionRaw': props.properties[1],
            'apiVersion': f'{(api >> 22) & 127}.{(api >> 12) & 1023}.{api & 4095}'})
    print(json.dumps(results, indent=2))
finally:
    vk.vkDestroyInstance(instance, None)

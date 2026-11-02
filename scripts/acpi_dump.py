import ctypes, ctypes.wintypes as wt, struct, os, re

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.EnumSystemFirmwareTables.argtypes = [wt.DWORD, ctypes.c_void_p, wt.DWORD]
k32.EnumSystemFirmwareTables.restype = wt.DWORD
k32.GetSystemFirmwareTable.argtypes = [wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD]
k32.GetSystemFirmwareTable.restype = wt.DWORD
sig = lambda s: struct.unpack('>I', s.encode('ascii'))[0]

def enum(p):
    n = k32.EnumSystemFirmwareTables(sig(p), None, 0)
    if not n: return [], n
    b = ctypes.create_string_buffer(n)
    k32.EnumSystemFirmwareTables(sig(p), b, n)
    return [struct.unpack('<I', b.raw[i:i+4])[0] for i in range(0, n, 4)], n

def fetch(p, tid):
    need = k32.GetSystemFirmwareTable(sig(p), tid, None, 0)
    if not need: return None
    b = ctypes.create_string_buffer(need)
    if k32.GetSystemFirmwareTable(sig(p), tid, b, need) == 0: return None
    return b.raw[:need]

ids, n = enum('ACPI')
print("enum bytes=%d -> %d ids" % (n, len(ids)))
print("ids:", ' '.join('%08X' % i for i in ids))

print("\n=== 直接取 DSDT (0x54445344) ===")
d = fetch('ACPI', 0x54445344)
print("DSDT fetch ->", None if d is None else len(d), "last_err=", ctypes.get_last_error())

print("\n=== 每张表的头 ===")
seen = {}
for tid in ids:
    d = fetch('ACPI', tid)
    if not d or len(d) < 36:
        print("  id=%08X  <short/none>" % tid); continue
    t = d[:4].decode('ascii', 'replace')
    ln = struct.unpack('<I', d[4:8])[0]
    rev = d[8]
    oem = d[10:16].decode('ascii','replace').strip('\x00 ')
    oemt = d[16:24].decode('ascii','replace').strip('\x00 ')
    cre = d[28:32].decode('ascii','replace').strip('\x00 ')
    crev = struct.unpack('<I', d[32:36])[0]
    key = (t, oem, oemt, crev)
    seen[key] = seen.get(key, 0) + 1
    print("  %-5s id=%08X len=%-6d rev=%d oem=%-10s tbl=%-10s cr=%s/%d" % (t, tid, len(d), rev, oem, oemt, cre, crev))

print("\n=== 去重后 ===")
for k, v in seen.items():
    print("  %-5s oem=%-10s tbl=%-10s crev=%d  x%d" % (k[0], k[1], k[2], k[3], v))

# 把 SSDT 里所有可打印串打出来（小表，值得看清）
print("\n=== 各表内可打印串 (len>=4) ===")
done = set()
for tid in ids:
    d = fetch('ACPI', tid)
    if not d: continue
    h = d[:36]
    key = h[10:24]
    if key in done: continue
    done.add(key)
    print("  --- %s %s" % (h[:4].decode('ascii','replace'), h[16:24].decode('ascii','replace')))
    body = d[36:]
    for m in re.finditer(rb'[\x20-\x7e]{4,}', body):
        s = m.group().decode()
        if s.strip(): print("      @%04X %s" % (m.start()+36, s[:70]))

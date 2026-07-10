import struct
path = r'D:\new_tdx\vipdoc\sh\minline\sh513120.lc1'
data = open(path, 'rb').read()

# 前5条记录
for i in range(0, min(160, len(data)), 32):
    chunk = data[i:i+32]
    # 尝试各种格式
    d_hi16 = struct.unpack('>H', chunk[2:4])[0]  # big-endian uint16 at offset 2
    d_lo16 = struct.unpack('<H', chunk[0:2])[0]   # little-endian uint16 at offset 0
    d_u32 = struct.unpack('<I', chunk[0:4])[0]     # little-endian uint32 at offset 0
    
    yr = d_lo16 // 2048 + 2000
    md = d_lo16 % 2048
    month = md // 100
    day = md % 100
    
    print(f'record {i//32}:')
    print(f'  raw[0:4] hex: {chunk[0:4].hex()}')
    print(f'  as u32 LE: {d_u32}  -> {"valid" if 20200101<=d_u32<=20991231 else "INVALID"}')
    print(f'  as u16 LE: {d_lo16} -> {yr}-{month:02d}-{day:02d}')
    print(f'  bytes[4:6] hex: {chunk[4:6].hex()}  as u16: {struct.unpack("<H", chunk[4:6])[0]}')
    print(f'  bytes[6:10]: {[round(x,2) for x in struct.unpack("f", chunk[6:10])]}')
    print()
    if i >= 128: break

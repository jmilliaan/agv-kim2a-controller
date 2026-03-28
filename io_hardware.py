import asyncio
import socket
import binascii
import struct
import time
import can
from pymodbus.client import AsyncModbusTcpClient
import serial.tools.list_ports as list_ports

import config

# ── Input loops ───────────────────────────────────────────────────────────────

async def di_reader(state):
    client = AsyncModbusTcpClient(config.DIO_IP, port=config.MODBUS_PORT)
    
    while True:
        try:
            if not client.connected:
                await client.connect()
                if not client.connected:
                    print(f"DIO not ready at {config.DIO_IP}. Retrying...")
                    await asyncio.sleep(2)
                    continue

            result = await client.read_discrete_inputs(address=config.DI_BASE, count=config.NUM_DI, device_id=config.DEVICE_ID)
            if not result.isError():
                state.latest_di = result.bits[:config.NUM_DI]
                # print(state.latest_di)
                await state.di_queue.put(state.latest_di)
            else:
                print("Modbus read error, reconnecting...")
                client.close()

        except Exception as e:
            print(f"DI Reader exception: {e}")
            client.close()
            
        await asyncio.sleep(0.05)

async def rfid_reader(state):
    loop = asyncio.get_event_loop()
    while True:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            await loop.sock_connect(sock, (config.RFID_IP, config.RFID_PORT))
            await loop.sock_sendall(sock, config.RFID_INIT_CMD)
            print("RFID Connected.")
            
            while True:
                data = await loop.sock_recv(sock, 1024)
                if not data:
                    print("RFID connection closed by peer.")
                    break
                
                hex_data = binascii.hexlify(data).decode().upper()
                for packet in hex_data.split("CF")[1:]:
                    packet = "CF" + packet
                    if len(packet) >= 34:
                        tag = packet[26:30]
                        print(f"RFID tag read: {tag} (dec={int(tag, 16)})")
                        await state.rfid_queue.put(tag)
                        
        except Exception as e:
            print(f"RFID connection failed/lost: {e}. Retrying in 2s...")
        finally:
            sock.close()
            
        await asyncio.sleep(2)

def find_canable_port(VID=0x16D0, PID=0X117E):
    ports = list_ports.comports()
    for port in ports:
        if port.vid == VID and port.pid == PID:
            print(f"Found at: {port.device}")
            return port.device

async def can_reader(state):
    while True:
        CAN_CHANNEL = find_canable_port()
        if CAN_CHANNEL is None:
            print("CANable2 not found, retrying in 2s...")
            await asyncio.sleep(2)
            continue
            
        bus = None
        notifier = None
        try:
            bus = can.interface.Bus(
                channel=CAN_CHANNEL,
                interface="slcan",
                bitrate=500_000,
                ttyBaudrate=3_000_000)
            reader = can.AsyncBufferedReader()
            
            notifier = can.Notifier(bus, [reader])
            bus.send(can.Message(
                arbitration_id=0x000,
                data=[0x01, config.CAN_NODE_ID],
                is_extended_id=False
            ))
            
            print(f"CAN connected on {CAN_CHANNEL}")
            state.can_last_rx = time.time()
            
            async for msg in reader:
                # FIX: Changed >= 6 to >= 5 to prevent dropping valid minimal payloads
                if msg.arbitration_id == config.SENSOR_COB_ID and len(msg.data) >= 5:
                    left, right = struct.unpack_from("<hh", msg.data, 0)
                    flags = msg.data[4]
                    state.can_last_rx = time.time()
                    await state.sensor_queue.put({
                        "left_mm":        left,
                        "right_mm":       right,
                        "tape_detected":  bool(flags & config.FLAG_TAPE_DETECT),
                        "sensor_failure": bool(flags & config.FLAG_SENSOR_FAIL),
                    })
                    
        except Exception as e:
            print(f"CAN error or disconnect: {e}, retrying in 2s...")
            
        finally:
            if notifier is not None:
                notifier.stop()
            if bus is not None:
                bus.shutdown()
                
            await asyncio.sleep(2)


# ── Output loops ──────────────────────────────────────────────────────────────

async def do_writer(state):
    client = AsyncModbusTcpClient(config.DIO_IP, port=config.MODBUS_PORT)
    while True:
        channel_no, active_state = await state.do_queue.get()
        try:
            if not client.connected:
                await client.connect()
            
            await client.write_coil(address=config.DO_BASE + channel_no, value=bool(active_state), device_id=config.DEVICE_ID)
        
        except Exception as e:
            print(f"DO Writer error: {e}. Reconnecting...")
            client.close()
            await asyncio.sleep(1)

async def ao_writer(state):
    client = AsyncModbusTcpClient(config.AO_IP, port=config.MODBUS_PORT)
    while True:
        channel_no, target_v = await state.ao_queue.get()
        dac_value = int(target_v / config.V_RANGE * config.DAC_RES)
        try:
            if not client.connected:
                await client.connect()
            
            await client.write_register(address=config.AO_BASE + channel_no, value=dac_value, device_id=config.DEVICE_ID)
            
        except Exception as e:
            print(f"AO Writer error: {e}. Reconnecting...")
            client.close()
            await asyncio.sleep(1)
            
# Read-only live chat reader for Conquer.exe (no ASLR, image base 0x400000).
#
# Finds the client's ChatLog object by its vtable (0x007FD464) and the "ChatLog.log" name at +0x120,
# then polls its pending-record vector (+0x230 begin, +0x234 end; 8-byte entries, first dword = record pointer).
# Record (0x230 bytes): [0] saved flag | +0x10 sender[16] | +0x20 target[16] | +0x30 text[512], plaintext.
#
# Uses only OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION) and ReadProcessMemory.
# It never writes to, injects into, or suspends the game. Output: one JSON object per line on stdout.

$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class ConquerChatMemory
{
    const uint PROCESS_VM_READ = 0x0010, PROCESS_QUERY_INFORMATION = 0x0400;
    const uint MEM_COMMIT = 0x1000, PAGE_GUARD = 0x100, PAGE_NOACCESS = 0x01;
    const uint VTABLE = 0x007FD464;
    const int NAME_OFFSET = 0x120, VEC_BEGIN = 0x230, VEC_END = 0x234, ENABLED = 0x240;
    const int RECORD_SIZE = 0x230, MAX_PENDING = 4096;
    // Hero singleton (FUN_0040445d returns *0x008D365C); the character name pointer is at +0x15C.
    const uint HERO_SINGLETON = 0x008D365C;
    const int HERO_NAME_PTR = 0x15C;

    [StructLayout(LayoutKind.Sequential)]
    struct MBI { public IntPtr BaseAddress, AllocationBase; public uint AllocationProtect; public IntPtr RegionSize; public uint State, Protect, Type; }

    [DllImport("kernel32", SetLastError = true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32", SetLastError = true)] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32", SetLastError = true)] static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, int size, out IntPtr read);
    [DllImport("kernel32", SetLastError = true)] static extern IntPtr VirtualQueryEx(IntPtr h, IntPtr addr, out MBI info, IntPtr len);

    class Client { public int Pid; public IntPtr Handle; public List<uint> Objects = new List<uint>(); public HashSet<string> Seen = new HashSet<string>(); public HashSet<uint> Primed = new HashSet<uint>(); public DateTime NextScan; public byte[] Character = new byte[0]; public DateTime NextNameRead; }

    static byte[] ReadCharacterName(IntPtr h)
    {
        var hero = Read(h, HERO_SINGLETON, 4);
        if (hero == null) return null;
        uint heroPtr = BitConverter.ToUInt32(hero, 0);
        if (heroPtr == 0) return null;
        var namePtr = Read(h, heroPtr + (uint)HERO_NAME_PTR, 4);
        if (namePtr == null) return null;
        uint p = BitConverter.ToUInt32(namePtr, 0);
        if (p == 0) return null;
        var name = Read(h, p, 16);
        return name == null ? null : CStr(name, 0, 16);
    }

    static byte[] Read(IntPtr h, ulong addr, int size)
    {
        var buf = new byte[size];
        IntPtr read;
        if (!ReadProcessMemory(h, (IntPtr)(long)addr, buf, size, out read) || (int)read != size) return null;
        return buf;
    }

    static string Json(string s)
    {
        var sb = new StringBuilder("\"");
        foreach (char c in s)
        {
            if (c == '"' || c == '\\') sb.Append('\\').Append(c);
            else if (c < 0x20) sb.AppendFormat("\\u{0:x4}", (int)c);
            else sb.Append(c);
        }
        return sb.Append('"').ToString();
    }

    static void Emit(string json) { Console.Out.WriteLine(json); Console.Out.Flush(); }
    static void Status(int pid, string msg) { Emit("{\"status\":" + Json(msg) + ",\"pid\":" + pid + "}"); }

    static byte[] CStr(byte[] b, int off, int len)
    {
        int end = Array.IndexOf(b, (byte)0, off, len);
        int n = (end < 0 ? len : end - off);
        var r = new byte[n];
        Array.Copy(b, off, r, 0, n);
        return r;
    }

    static List<uint> FindChatLogObjects(IntPtr h)
    {
        var found = new List<uint>();
        var marker = Encoding.ASCII.GetBytes("ChatLog.log\0");
        ulong addr = 0x10000;
        int mbiSize = Marshal.SizeOf(typeof(MBI));
        while (addr < 0xFFFF0000UL)
        {
            MBI m;
            if (VirtualQueryEx(h, (IntPtr)(long)addr, out m, (IntPtr)mbiSize) == IntPtr.Zero) break;
            ulong baseAddr = (ulong)(long)m.BaseAddress, size = (ulong)(long)m.RegionSize;
            if (size == 0) break;
            bool readable = m.State == MEM_COMMIT && (m.Protect & PAGE_GUARD) == 0 && (m.Protect & PAGE_NOACCESS) == 0 && (m.Protect & 0x66) != 0;
            if (readable)
            {
                const ulong CHUNK = 8 * 1024 * 1024;
                for (ulong off = 0; off < size; off += CHUNK)
                {
                    int len = (int)Math.Min(CHUNK, size - off);
                    var buf = Read(h, baseAddr + off, len);
                    if (buf == null) continue;
                    for (int i = 0; i + 4 <= len; i += 4)
                    {
                        if (BitConverter.ToUInt32(buf, i) != VTABLE) continue;
                        ulong obj = baseAddr + off + (ulong)i;
                        var name = Read(h, obj + NAME_OFFSET, marker.Length);
                        bool match = name != null;
                        for (int k = 0; match && k < marker.Length; k++) match = name[k] == marker[k];
                        if (match) found.Add((uint)obj);
                    }
                }
            }
            addr = baseAddr + size;
        }
        return found;
    }

    static void Poll(Client c)
    {
        if (DateTime.UtcNow >= c.NextNameRead)
        {
            var name = ReadCharacterName(c.Handle);
            c.NextNameRead = DateTime.UtcNow.AddSeconds(5);
            if (name != null && Convert.ToBase64String(name) != Convert.ToBase64String(c.Character))
            {
                c.Character = name;
                Status(c.Pid, "character: " + Encoding.GetEncoding(1252).GetString(name));
            }
        }
        foreach (uint obj in c.Objects.ToArray())
        {
            var head = Read(c.Handle, obj, ENABLED + 4);
            if (head == null || BitConverter.ToUInt32(head, 0) != VTABLE) { c.Objects.Remove(obj); c.Primed.Remove(obj); Status(c.Pid, "chat object lost; rescanning"); c.NextScan = DateTime.MinValue; continue; }
            // The records already there when a chat object is first read are history (from before
            // the script started / before this login): remember them without emitting them.
            bool history = c.Primed.Add(obj);
            uint begin = BitConverter.ToUInt32(head, VEC_BEGIN), end = BitConverter.ToUInt32(head, VEC_END);
            if (begin == 0 || end <= begin) continue;
            int count = (int)((end - begin) / 8);
            if (count > MAX_PENDING) continue;
            var entries = Read(c.Handle, begin, count * 8);
            if (entries == null) continue;
            for (int i = 0; i < count; i++)
            {
                uint rec = BitConverter.ToUInt32(entries, i * 8);
                if (rec == 0) continue;
                var r = Read(c.Handle, rec, RECORD_SIZE);
                if (r == null) continue;
                var text = CStr(r, 0x30, 0x200);
                if (text.Length == 0) continue;
                string key = rec.ToString("x8") + ":" + Convert.ToBase64String(text);
                if (!c.Seen.Add(key) || history) continue;
                Emit("{\"pid\":" + c.Pid +
                     ",\"character\":" + Json(Convert.ToBase64String(c.Character)) +
                     ",\"sender\":" + Json(Convert.ToBase64String(CStr(r, 0x10, 0x10))) +
                     ",\"target\":" + Json(Convert.ToBase64String(CStr(r, 0x20, 0x10))) +
                     ",\"text\":" + Json(Convert.ToBase64String(text)) + "}");
            }
            if (c.Seen.Count > 20000) { c.Seen.Clear(); c.Primed.Clear(); }   // re-prime: never re-emit what's there
        }
    }

    public static void Run(int pollMs)
    {
        var clients = new Dictionary<int, Client>();
        while (true)
        {
            var alive = new HashSet<int>();
            foreach (var p in Process.GetProcessesByName("Conquer"))
            {
                alive.Add(p.Id);
                Client c;
                if (!clients.TryGetValue(p.Id, out c))
                {
                    IntPtr h = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, false, p.Id);
                    if (h == IntPtr.Zero) { Status(p.Id, "OpenProcess denied (error " + Marshal.GetLastWin32Error() + "); not retrying this process"); clients[p.Id] = new Client { Pid = p.Id, Handle = IntPtr.Zero }; continue; }
                    c = new Client { Pid = p.Id, Handle = h, NextScan = DateTime.MinValue };
                    clients[p.Id] = c;
                    Status(p.Id, "attached (read-only)");
                }
                if (c.Handle == IntPtr.Zero) continue;
                if (c.Objects.Count == 0 && DateTime.UtcNow >= c.NextScan)
                {
                    c.Objects = FindChatLogObjects(c.Handle);
                    c.NextScan = DateTime.UtcNow.AddSeconds(10);
                    Status(c.Pid, c.Objects.Count == 0 ? "chat object not found yet (log in first); retrying in 10s" : "chat object found at 0x" + string.Join(",0x", c.Objects.ConvertAll(o => o.ToString("x8")).ToArray()));
                }
                Poll(c);
            }
            foreach (var pid in new List<int>(clients.Keys))
            {
                if (alive.Contains(pid)) continue;
                if (clients[pid].Handle != IntPtr.Zero) CloseHandle(clients[pid].Handle);
                clients.Remove(pid);
                Status(pid, "process exited");
            }
            Thread.Sleep(pollMs);
        }
    }
}
'@

[ConquerChatMemory]::Run(500)

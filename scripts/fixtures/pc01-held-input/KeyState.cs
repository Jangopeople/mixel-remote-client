using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Security.Principal;
using System.Runtime.InteropServices;
using System.Text;

// Read-only observations. No input, process control, policy or security writes.
public static class MixelHeldInputReadOnly {
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll", SetLastError=true)]
    static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll", SetLastError=true)] static extern bool CloseDesktop(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr GetThreadDesktop(uint thread);
    [DllImport("user32.dll")] static extern IntPtr GetProcessWindowStation();
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("kernel32.dll")] static extern ulong GetTickCount64();
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool GetUserObjectInformation(IntPtr h, int index, StringBuilder text,
                                               uint length, out uint needed);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentThread();
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr h);
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool OpenProcessToken(IntPtr p, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool OpenThreadToken(IntPtr p, uint access, bool self, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool GetTokenInformation(IntPtr t, int kind, IntPtr data, int size, out int actual);
    [DllImport("user32.dll", SetLastError=true)]
    static extern bool GetUserObjectSecurity(IntPtr h, ref uint info, [Out] byte[] data, uint size, out uint actual);
    static IntPtr TokenData(IntPtr token, int kind, out int size) {
        int needed;
        bool first=GetTokenInformation(token,kind,IntPtr.Zero,0,out needed);
        if(first || Marshal.GetLastWin32Error()!=122 || needed<4 || needed>65536)
            throw new InvalidOperationException("Read-only token bounds");
        IntPtr p=Marshal.AllocHGlobal(needed);
        int actual;
        if(!GetTokenInformation(token,kind,p,needed,out actual) || actual<4 || actual>needed) {
            Marshal.FreeHGlobal(p); throw new Win32Exception();
        }
        size=actual;return p;
    }
    static int Scalar(IntPtr token,int kind) {
        int size;IntPtr p=TokenData(token,kind,out size);
        try { if(size!=4)throw new InvalidOperationException("Token scalar size");return Marshal.ReadInt32(p); }
        finally { Marshal.FreeHGlobal(p); }
    }
    static string BoundedSid(IntPtr root,int size,IntPtr sid) {
        long offset=sid.ToInt64()-root.ToInt64();
        if(offset<0 || offset>size-8 || Marshal.ReadByte(sid)!=1)
            throw new InvalidOperationException("Token SID bounds");
        int count=Marshal.ReadByte(sid,1);int length=8+4*count;
        if(count>15 || length>size-offset)throw new InvalidOperationException("Token SID size");
        byte[] raw=new byte[length];Marshal.Copy(sid,raw,0,length);
        return new SecurityIdentifier(raw,0).Value;
    }
    static string UserSid(IntPtr token) {
        int size;IntPtr p=TokenData(token,1,out size);
        try { if(size<IntPtr.Size+4)throw new InvalidOperationException("Token user size");
              return BoundedSid(p,size,Marshal.ReadIntPtr(p)); }
        finally { Marshal.FreeHGlobal(p); }
    }
    static void TokenSids(IntPtr token,HashSet<string> allow,HashSet<string> deny) {
        string user=UserSid(token);allow.Add(user);deny.Add(user);
        int size;IntPtr p=TokenData(token,2,out size);
        try {
            int count=Marshal.ReadInt32(p),offset=IntPtr.Size,stride=IntPtr.Size==8?16:8;
            if(count<0 || count>256 || (long)offset+(long)count*stride>size)
                throw new InvalidOperationException("Token group bounds");
            for(int i=0;i<count;i++) {
                int at=offset+i*stride;uint flags=(uint)Marshal.ReadInt32(p,at+IntPtr.Size);
                string sid=BoundedSid(p,size,Marshal.ReadIntPtr(p,at));
                if((flags&4)!=0 && (flags&16)==0)allow.Add(sid); // enabled, not deny-only
                if((flags&(4|16))!=0)deny.Add(sid);
            }
        } finally { Marshal.FreeHGlobal(p); }
    }
    static uint ObservationBits(uint mask) {
        // Official desktop generic-write/all mappings both include these bits.
        if((mask&(0x10000000U|0x40000000U))!=0)mask|=0x18;
        return mask&0x18;
    }
    static void ExistingAccess(IntPtr desktop) {
        IntPtr thread=IntPtr.Zero,token=IntPtr.Zero;
        // An impersonated caller is outside this same-owner observation contract.
        if(OpenThreadToken(GetCurrentThread(),8,false,out thread)) {
            try { throw new InvalidOperationException("Impersonated observer refused"); }
            finally { if(!CloseHandle(thread))throw new Win32Exception(); }
        }
        if(Marshal.GetLastWin32Error()!=1008)throw new Win32Exception();
        if(!OpenProcessToken(GetCurrentProcess(),8,out token))throw new Win32Exception();
        try {
            if(Scalar(token,21)!=0 || Scalar(token,29)!=0 || Scalar(token,20)!=0)
                throw new InvalidOperationException("Restricted/appcontainer/elevated observer refused");
            int labelSize;IntPtr label=TokenData(token,25,out labelSize);
            try {
                if(labelSize<IntPtr.Size+4 || BoundedSid(label,labelSize,Marshal.ReadIntPtr(label))!="S-1-16-8192")
                    throw new InvalidOperationException("Observer must be actual Medium");
            } finally { Marshal.FreeHGlobal(label); }
            var allow=new HashSet<string>(StringComparer.Ordinal);
            var deny=new HashSet<string>(StringComparer.Ordinal);TokenSids(token,allow,deny);
            uint info=4,needed;
            bool first=GetUserObjectSecurity(desktop,ref info,null,0,out needed);
            if(first || Marshal.GetLastWin32Error()!=122 || needed<20 || needed>65536)
                throw new InvalidOperationException("Existing desktop DACL is not readable");
            byte[] sd=new byte[needed];uint actual;
            if(!GetUserObjectSecurity(desktop,ref info,sd,needed,out actual) || actual<20 || actual>needed)
                throw new Win32Exception();
            ushort control=BitConverter.ToUInt16(sd,2);
            uint offset=BitConverter.ToUInt32(sd,16);
            if(sd[0]!=1 || (control&0x8004)!=0x8004 || offset<20 || offset>actual-8)
                throw new InvalidOperationException("Closed self-relative DACL required");
            int at=(int)offset;int length=BitConverter.ToUInt16(sd,at+2),count=BitConverter.ToUInt16(sd,at+4);
            if(length<8 || offset+(uint)length>actual || count>256)
                throw new InvalidOperationException("Desktop ACL bounds");
            int end=at+length;at+=8;uint granted=0;
            for(int i=0;i<count;i++) {
                if(at>end-4)throw new InvalidOperationException("Desktop ACE header");
                byte type=sd[at],flags=sd[at+1];int size=BitConverter.ToUInt16(sd,at+2);
                if(size<4 || size>end-at)throw new InvalidOperationException("Desktop ACE bounds");
                if((flags&8)!=0) { at+=size;continue; } // inherit-only does not apply here
                if((type!=0 && type!=1) || size<16 || sd[at+8]!=1)
                    throw new InvalidOperationException("Unknown desktop ACE refused");
                int sidLength=8+4*sd[at+9];
                if(sd[at+9]>15 || sidLength>size-8)throw new InvalidOperationException("ACE SID bounds");
                string sid=new SecurityIdentifier(sd,at+8).Value;
                if(sid.StartsWith("S-1-3-",StringComparison.Ordinal) || sid=="S-1-5-10")
                    throw new InvalidOperationException("Context-dependent desktop trustee refused");
                uint bits=ObservationBits(BitConverter.ToUInt32(sd,at+4));
                // Conservatively reject any applicable deny, even a late deny
                // in a noncanonical ACL. Never change ACLs or acquire hook rights.
                if(type==1 && deny.Contains(sid) && bits!=0)
                    throw new InvalidOperationException("Desktop observation access denied");
                if(type==0 && allow.Contains(sid))granted|=bits;
                at+=size;
            }
            if(granted!=0x18)throw new InvalidOperationException("Existing background key-query access unproven");
        } finally { if(token!=IntPtr.Zero && !CloseHandle(token))throw new Win32Exception(); }
    }
    static string DesktopName(IntPtr h) {
        uint needed;
        var text = new StringBuilder(256);
        if (h == IntPtr.Zero || !GetUserObjectInformation(h, 2, text, 512, out needed))
            throw new Win32Exception();
        if (needed < 2 || needed > 512) throw new InvalidOperationException("Desktop name bound");
        return text.ToString();
    }
    public sealed class Facts {
        public bool shift, leftShift, rightShift, leftButton, rightButton;
        public string inputDesktop, observerDesktop;
        public ulong monotonicMilliseconds;
        public uint foregroundPid;
    }
    public static Facts Context() {
        IntPtr current=OpenInputDesktop(0,false,1);
        if(current==IntPtr.Zero)throw new Win32Exception();
        try {
            string input=DesktopName(current),own=DesktopName(GetThreadDesktop(GetCurrentThreadId()));
            if(input!="Default" || own!=input || DesktopName(GetProcessWindowStation())!="WinSta0")throw new InvalidOperationException("Active Default desktop required");
            ExistingAccess(GetThreadDesktop(GetCurrentThreadId()));
            uint pid;IntPtr hwnd=GetForegroundWindow();
            if(hwnd==IntPtr.Zero || GetWindowThreadProcessId(hwnd,out pid)==0)
                throw new InvalidOperationException("Actual foreground required");
            return new Facts {inputDesktop=input,observerDesktop=own,foregroundPid=pid,
                              monotonicMilliseconds=GetTickCount64()};
        } finally { if(!CloseDesktop(current))throw new Win32Exception(); }
    }
    public static Facts Observe() {
        // Requiring an accessible, matching Default input desktop prevents an
        // inaccessible/secure desktop's zero result from being called release.
        IntPtr input = OpenInputDesktop(0, false, 1); // DESKTOP_READOBJECTS only
        if (input == IntPtr.Zero) throw new Win32Exception();
        try {
            string before = DesktopName(input);
            string own = DesktopName(GetThreadDesktop(GetCurrentThreadId()));
            if (before != "Default" || own != before || DesktopName(GetProcessWindowStation())!="WinSta0")
                throw new InvalidOperationException("Accessible Default desktop required");
            ExistingAccess(GetThreadDesktop(GetCurrentThreadId()));
            uint foreground;
            IntPtr hwnd = GetForegroundWindow();
            if (hwnd == IntPtr.Zero || GetWindowThreadProcessId(hwnd, out foreground) == 0)
                throw new InvalidOperationException("Live input desktop foreground required");
            var f = new Facts {
                shift=(GetAsyncKeyState(0x10) & 0x8000)!=0,
                leftShift=(GetAsyncKeyState(0xA0) & 0x8000)!=0,
                rightShift=(GetAsyncKeyState(0xA1) & 0x8000)!=0,
                leftButton=(GetAsyncKeyState(0x01) & 0x8000)!=0,
                rightButton=(GetAsyncKeyState(0x02) & 0x8000)!=0,
                inputDesktop=before, observerDesktop=own,
                monotonicMilliseconds=GetTickCount64(), foregroundPid=foreground
            };
            // Re-open the CURRENT input desktop. Querying the old retained
            // handle again would still say Default after a secure switch.
            IntPtr after = OpenInputDesktop(0, false, 1);
            if (after == IntPtr.Zero) throw new Win32Exception();
            try {
                uint finalPid;IntPtr finalWindow=GetForegroundWindow();
                if (DesktopName(after) != before || finalWindow==IntPtr.Zero ||
                    GetWindowThreadProcessId(finalWindow,out finalPid)==0 || finalPid!=foreground)
                    throw new InvalidOperationException("Desktop/foreground changed during sample");
                ExistingAccess(GetThreadDesktop(GetCurrentThreadId()));
            } finally { if (!CloseDesktop(after)) throw new Win32Exception(); }
            return f;
        } finally { if (!CloseDesktop(input)) throw new Win32Exception(); }
    }
}

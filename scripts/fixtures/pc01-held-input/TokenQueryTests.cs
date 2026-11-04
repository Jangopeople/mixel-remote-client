using System;
using System.ComponentModel;
using System.Reflection;
using System.Runtime.InteropServices;

// Compile beside KeyState.cs. Default mode never invokes Windows APIs.
public static class MixelTokenQueryTests {
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);

    static object Call(string method,params object[] args) {
        try {
            return typeof(MixelHeldInputReadOnly).GetMethod(method,
                BindingFlags.NonPublic|BindingFlags.Static).Invoke(null,args);
        } catch(TargetInvocationException error) { throw error.InnerException; }
    }
    static int Inert() {
        int controls=0;
        foreach(int size in new[]{4,8,24}) {
            MixelHeldInputReadOnly.RequireNoRestrictedSids(size,0);controls++;
        }
        foreach(int size in new[]{0,3,65537}) {
            bool refused=false;
            try { MixelHeldInputReadOnly.RequireNoRestrictedSids(size,0); }
            catch(InvalidOperationException) { refused=true; }
            if(!refused)throw new Exception("Malformed group buffer admitted");controls++;
        }
        foreach(int count in new[]{-1,1,4096}) {
            bool refused=false;
            try { MixelHeldInputReadOnly.RequireNoRestrictedSids(24,count); }
            catch(InvalidOperationException) { refused=true; }
            if(!refused)throw new Exception("Restricted group list admitted");controls++;
        }
        Console.WriteLine("PASS_INERT_TOKEN_QUERY_CONTROLS "+controls+"; no Windows APIs called");
        return 0;
    }
    static int NativeOwnToken() {
        if(Environment.OSVersion.Platform!=PlatformID.Win32NT||!Environment.Is64BitProcess)
            throw new InvalidOperationException("Native x64 Windows required");
        IntPtr token;
        if(!OpenProcessToken(GetCurrentProcess(),8,out token))throw new Win32Exception();
        try {
            // Exact production observer methods; no copied ABI implementation.
            Call("NoRestrictedSids",token);
            int app=(int)Call("Scalar",token,29),elevated=(int)Call("Scalar",token,20);
            if((app!=0&&app!=1)||(elevated!=0&&elevated!=1))
                throw new Exception("Unexpected native scalar value");
            object[] labelArgs={token,25,0};
            IntPtr label=(IntPtr)Call("TokenData",labelArgs);
            string integrity;
            try { integrity=(string)Call("BoundedSid",label,(int)labelArgs[2],Marshal.ReadIntPtr(label)); }
            finally { Marshal.FreeHGlobal(label); }
            bool invalidRefused=false;
            try { Call("Scalar",token,int.MaxValue); }
            catch(Win32Exception) { invalidRefused=true; }
            if(!invalidRefused)throw new Exception("Invalid native query admitted");
            Console.WriteLine("PASS_NATIVE_OWN_TOKEN_QUERY_REGRESSION appContainer="+app+
                ";elevated="+elevated+";integrity="+integrity+
                ";restrictedSids=0; invalidQueryRefused=true; receiverQualification=false");
            return 0;
        } finally { if(!CloseHandle(token))throw new Win32Exception(); }
    }
    public static int Main(string[] args) {
        try {
            if(args.Length==0){Console.WriteLine("PLAN ONLY: --self-test or --native-own-token. No calls or writes.");return 0;}
            if(args.Length==1&&args[0]=="--self-test")return Inert();
            if(args.Length==1&&args[0]=="--native-own-token")return NativeOwnToken();
            throw new InvalidOperationException("Exact explicit mode required");
        } catch(Exception error){Console.Error.WriteLine("REFUSE_TOKEN_QUERY_TEST "+error);return 1;}
    }
}

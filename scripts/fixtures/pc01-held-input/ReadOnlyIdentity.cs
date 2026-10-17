using System ; using System . Collections . Generic ; using System . ComponentModel ; using System .
Diagnostics ; using System . IO ; using System . IO . Pipes ; using System . Runtime .
InteropServices ; using System . Security . Principal ; using System . Text ; using Microsoft .
Win32 . SafeHandles ; public static class MixelPc01ReadOnly070 { [ DllImport ( "kernel32.dll" ,
SetLastError = true ) ] static extern IntPtr OpenProcess ( uint access , bool inherit , int pid ) ;
[ DllImport ( "kernel32.dll" , SetLastError = true ) ] static extern bool CloseHandle ( IntPtr
handle ) ; [ DllImport ( "kernel32.dll" , SetLastError = true ) ] static extern uint GetProcessId (
IntPtr handle ) ; [ DllImport ( "kernel32.dll" , SetLastError = true ) ] static extern uint
WaitForSingleObject ( IntPtr handle , uint timeout ) ; [ DllImport ( "kernel32.dll" , CharSet =
CharSet . Unicode , SetLastError = true ) ] static extern bool QueryFullProcessImageName ( IntPtr
handle , uint flags , StringBuilder path , ref uint size ) ; [ DllImport ( "kernel32.dll" ,
SetLastError = true ) ] static extern bool GetProcessTimes ( IntPtr handle , out long creation , out
long exit , out long kernel , out long user ) ; [ DllImport ( "advapi32.dll" , SetLastError = true )
] static extern bool OpenProcessToken ( IntPtr process , uint access , out IntPtr token ) ; [
DllImport ( "advapi32.dll" , SetLastError = true ) ] static extern bool GetTokenInformation ( IntPtr
token , int kind , IntPtr buffer , int size , out int required ) ; [ DllImport ( "kernel32.dll" ,
SetLastError = true ) ] static extern bool GetNamedPipeServerProcessId ( SafePipeHandle pipe , out
uint processId ) ; public sealed class Facts { public int pid , session , integrity ; public long
birth ; public bool elevated ; public string sid , path ; } static readonly Dictionary < int ,
IntPtr > handles = new Dictionary < int , IntPtr > ( ) ; static int Scalar ( IntPtr token , int kind
) { IntPtr data = Marshal . AllocHGlobal ( 4 ) ; try { int actual ; if ( ! GetTokenInformation (
token , kind , data , 4 , out actual ) || actual != 4 ) throw new Win32Exception ( ) ; return
Marshal . ReadInt32 ( data ) ; } finally { Marshal . FreeHGlobal ( data ) ; } } public static Facts
Observe ( int pid ) { if ( pid <= 0 ) throw new InvalidDataException (
"Positive observed PID required" ) ; IntPtr process ; bool fresh = ! handles . TryGetValue ( pid ,
out process ) ; if ( fresh ) process = OpenProcess ( 0x101000 , false , pid ) ; if ( process ==
IntPtr . Zero ) throw new Win32Exception ( ) ; IntPtr token = IntPtr . Zero , label = IntPtr . Zero
; try { if ( GetProcessId ( process ) != ( uint ) pid || WaitForSingleObject ( process , 0 ) !=
0x102 ) throw new InvalidOperationException ( "Observed PID exited or changed" ) ; if ( !
OpenProcessToken ( process , 8 , out token ) ) throw new Win32Exception ( ) ; var f = new Facts {
pid = pid , session = Scalar ( token , 12 ) , elevated = Scalar ( token , 20 ) != 0 } ; using ( var
identity = new WindowsIdentity ( token ) ) { f . sid = identity . User . Value ; } int size ; bool
initial = GetTokenInformation ( token , 25 , IntPtr . Zero , 0 , out size ) ; if ( initial ||
Marshal . GetLastWin32Error ( ) != 122 || size < IntPtr . Size + 4 || size > 65536 ) throw new
InvalidDataException ( "Integrity query bounds" ) ; label = Marshal . AllocHGlobal ( size ) ; int
actual ; if ( ! GetTokenInformation ( token , 25 , label , size , out actual ) || actual < IntPtr .
Size + 4 || actual > size ) throw new Win32Exception ( ) ; IntPtr sidPointer = Marshal . ReadIntPtr
( label ) ; long offset = sidPointer . ToInt64 ( ) - label . ToInt64 ( ) ; if ( offset < 0 || offset
> actual - 8 ) throw new InvalidDataException ( "Integrity SID bounds" ) ; int count = Marshal .
ReadByte ( sidPointer , 1 ) ; int bytes = 8 + 4 * count ; if ( count > 15 || bytes > actual - offset
) throw new InvalidDataException ( "Integrity SID size" ) ; byte [ ] sidBytes = new byte [ bytes ] ;
Marshal . Copy ( sidPointer , sidBytes , 0 , bytes ) ; string integritySid = new SecurityIdentifier
( sidBytes , 0 ) . Value ; if ( ! integritySid . StartsWith ( "S-1-16-" , StringComparison . Ordinal
) ) throw new InvalidDataException ( "Integrity authority" ) ; f . integrity = int . Parse (
integritySid . Substring ( 7 ) ) ; uint length = 32768 ; var path = new StringBuilder ( ( int )
length ) ; if ( ! QueryFullProcessImageName ( process , 0 , path , ref length ) ) throw new
Win32Exception ( ) ; f . path = path . ToString ( ) ; long exit , kernel , user ; if ( !
GetProcessTimes ( process , out f . birth , out exit , out kernel , out user ) ) throw new
Win32Exception ( ) ; if ( f . birth <= 0 || WaitForSingleObject ( process , 0 ) != 0x102 ) throw new
InvalidOperationException ( "Observed process exited" ) ; if ( fresh ) { handles . Add ( pid ,
process ) ; process = IntPtr . Zero ; } return f ; } finally { if ( label != IntPtr . Zero ) Marshal
. FreeHGlobal ( label ) ; int closeError = 0 ; if ( token != IntPtr . Zero && ! CloseHandle ( token
) ) closeError = Marshal . GetLastWin32Error ( ) ; if ( fresh && process != IntPtr . Zero && !
CloseHandle ( process ) && closeError == 0 ) closeError = Marshal . GetLastWin32Error ( ) ; if (
closeError != 0 ) throw new Win32Exception ( closeError ) ; } } public static void CloseObservations
( ) { var owned = new List < IntPtr > ( handles . Values ) ; handles . Clear ( ) ; Exception first =
null ; foreach ( var h in owned ) if ( ! CloseHandle ( h ) && first == null ) first = new
Win32Exception ( ) ; if ( first != null ) throw first ; } public sealed class Response { public
string Json ; public uint ServerPid ; } static readonly string [ ] allowed = {
"{\"t\":\"Config\",\"c\":[\"mixel-support-invite-attended\",null]}" ,
"{\"t\":\"Config\",\"c\":[\"mixel-support-invite-lease-status\",null]}" ,
"{\"t\":\"OnlineStatus\",\"c\":null}" , "{\"t\":\"Config\",\"c\":[\"rendezvous_server\",null]}" ,
"{\"t\":\"Config\",\"c\":[\"id\",null]}" , "{\"t\":\"VideoConnCount\",\"c\":null}" } ; static int
Remaining ( Stopwatch clock ) { long left = 6000 - clock . ElapsedMilliseconds ; if ( left <= 0 )
throw new TimeoutException ( "Read-only IPC deadline" ) ; return ( int ) Math . Min ( 2000 , left )
; } static void ReadExact ( Stream stream , byte [ ] buffer , Stopwatch clock ) { int offset = 0 ;
while ( offset < buffer . Length ) { var read = stream . ReadAsync ( buffer , offset , buffer .
Length - offset ) ; if ( ! read . Wait ( Remaining ( clock ) ) ) throw new TimeoutException (
"Read-only IPC read timeout" ) ; if ( read . Result == 0 ) throw new IOException (
"Read-only IPC closed" ) ; offset += read . Result ; } } public static Response RequestWithServerPid
( string json , uint expectedPid ) { if ( expectedPid == 0 || Array . IndexOf ( allowed , json ) < 0
) throw new InvalidDataException ( "Only six literal read-only requests allowed" ) ; var clock =
Stopwatch . StartNew ( ) ; using ( var pipe = new NamedPipeClientStream ( "." ,
"Mixel-Remote\\query" , PipeDirection . InOut , PipeOptions . Asynchronous ) ) { pipe . Connect (
Remaining ( clock ) ) ; uint pid ; if ( ! GetNamedPipeServerProcessId ( pipe . SafePipeHandle , out
pid ) || pid != expectedPid ) throw new InvalidDataException ( "Unexpected kernel pipe owner" ) ;
var body = Encoding . UTF8 . GetBytes ( json ) ; if ( body . Length > 16383 ) throw new
InvalidDataException ( "Request bound" ) ; int headerLength = body . Length <= 63 ? 1 : 2 ; int
header = ( body . Length << 2 ) | ( headerLength - 1 ) ; var packet = new byte [ headerLength + body
. Length ] ; packet [ 0 ] = ( byte ) header ; if ( headerLength == 2 ) packet [ 1 ] = ( byte ) (
header >> 8 ) ; Buffer . BlockCopy ( body , 0 , packet , headerLength , body . Length ) ; var write
= pipe . WriteAsync ( packet , 0 , packet . Length ) ; if ( ! write . Wait ( Remaining ( clock ) ) )
throw new TimeoutException ( "Read-only IPC write timeout" ) ; var first = new byte [ 1 ] ;
ReadExact ( pipe , first , clock ) ; int responseHeaderLength = ( first [ 0 ] & 3 ) + 1 ; int
responseHeader = first [ 0 ] ; for ( int index = 1 ; index < responseHeaderLength ; index ++ ) { var
next = new byte [ 1 ] ; ReadExact ( pipe , next , clock ) ; responseHeader |= next [ 0 ] << ( 8 *
index ) ; } int size = responseHeader >> 2 ; if ( size < 0 || size > 65536 ) throw new
InvalidDataException ( "Response bound" ) ; var response = new byte [ size ] ; ReadExact ( pipe ,
response , clock ) ; uint after ; if ( ! GetNamedPipeServerProcessId ( pipe . SafePipeHandle , out
after ) || after != pid ) throw new InvalidDataException ( "Pipe owner changed" ) ; return new
Response { Json = new UTF8Encoding ( false , true ) . GetString ( response ) , ServerPid = pid } ; }
} }

// LAB: live checks of the Windows app's engine and view models against the live Mixel service.
//   MixelRemote.LabCheck check <qa-session.json> <suffix> <report>
//   MixelRemote.LabCheck technician <qa-session.json> <suffix> <folder> [host-id]
//   MixelRemote.LabCheck live <qa-session.json> <suffix> <folder> [last]
return await MixelRemote.Lab.LiveCheck.Main(args, line => Console.WriteLine(line));

on run {daemon_file, agent_file, user, cur_pid, source_dir, update_shell, app_name, bundle_id, with_service}
  set sh to "/bin/sh -c " & quoted form of update_shell & " -- " & quoted form of source_dir & " " & quoted form of user & " " & quoted form of cur_pid & " " & quoted form of app_name & " " & quoted form of bundle_id & " " & quoted form of with_service & " " & quoted form of daemon_file & " " & quoted form of agent_file
  do shell script sh with prompt app_name & " wants to update itself" with administrator privileges
end run

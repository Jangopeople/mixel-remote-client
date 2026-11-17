#!/bin/bash
# gcmd.sh <vm> <line...>: one lab command for the GTK lab app of gui.sh (see lab_hook.rs): connect <id> [attended] | end <id> | retry <id> | burst <id>... | chat <id> <text> | call <command> <json>
VM=$1; shift; "$(dirname "$0")/vx.sh" $VM "echo '$*' >> /root/gui/cmd.tmp && mv /root/gui/cmd.tmp /root/gui/cmd"

fn helper_zero(n:usize) {
                                        if n == 0 {
                                            log::info!("Connection count equals 0, exit");
                                            crate::input_service::release_all_owned_mouse_buttons();
                                            publish_will_close();
                                            return;
                                        }
                                    }

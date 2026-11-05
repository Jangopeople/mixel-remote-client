fn main() {
    protobuf_codegen::Codegen::new().pure().cargo_out_dir("protos")
        .include("protos").input("protos/message.proto").run_from_script();
}

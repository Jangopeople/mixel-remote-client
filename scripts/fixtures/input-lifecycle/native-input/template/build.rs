fn main() {
    protobuf_codegen::Codegen::new().pure().cargo_out_dir("protos")
        .input("protos/message.proto").include("protos")
        .customize(protobuf_codegen::Customize::default().tokio_bytes(true))
        .run().expect("exact production message.proto code generation failed");
}

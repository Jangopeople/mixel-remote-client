//! TURN TCP/TLS framing, RFC8656 §12.4–12.5 / RFC8489 §14.
//! STUN carries its own length; ChannelData needs four-byte stream padding.
//! Never wrap TURN in the unrelated RFC4571 ICE-TCP two-byte packet prefix.
use std::io;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
pub const MAX_PACKET: usize = 65535;
fn invalid() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "Malformed or oversized TURN stream packet",
    )
}
fn lengths(header: &[u8; 4]) -> io::Result<(usize, usize)> {
    let kind = u16::from_be_bytes([header[0], header[1]]);
    let length = usize::from(u16::from_be_bytes([header[2], header[3]]));
    let (raw, padded) = if kind & 0xc000 == 0 {
        if !length.is_multiple_of(4) {
            return Err(invalid());
        }
        (20 + length, 20 + length)
    } else if (0x4000..=0x4fff).contains(&kind) {
        (4 + length, (4 + length).next_multiple_of(4))
    } else {
        return Err(invalid());
    };
    if padded > MAX_PACKET {
        return Err(invalid());
    }
    Ok((raw, padded))
}
/// Accept one UDP-form TURN packet, normalize stream padding and reject extra
/// embedded packets. This validates framing, not STUN authentication semantics.
pub fn stream_packet(packet: &[u8]) -> io::Result<Vec<u8>> {
    let header: [u8; 4] = packet
        .get(..4)
        .ok_or_else(invalid)?
        .try_into()
        .map_err(|_| invalid())?;
    let (raw, padded) = lengths(&header)?;
    if packet.len() != raw && packet.len() != padded {
        return Err(invalid());
    }
    if header[0] & 0xc0 == 0 && packet.get(4..8) != Some(&[0x21, 0x12, 0xa4, 0x42]) {
        return Err(invalid());
    }
    let mut output = packet[..raw].to_vec();
    output.resize(padded, 0);
    Ok(output)
}
/// Read one complete packet; truncated headers/bodies never escape to WebRTC.
/// Channel padding is consumed here and omitted from the returned UDP form.
pub async fn read_packet<R: AsyncRead + Unpin>(reader: &mut R) -> io::Result<Vec<u8>> {
    let mut header = [0; 4];
    reader.read_exact(&mut header).await?;
    let (raw, padded) = lengths(&header)?;
    let mut packet = vec![0; padded];
    packet[..4].copy_from_slice(&header);
    if header[0] & 0xc0 == 0 {
        reader.read_exact(&mut packet[4..20]).await?;
        if packet[4..8] != [0x21, 0x12, 0xa4, 0x42] {
            return Err(invalid());
        }
        reader.read_exact(&mut packet[20..]).await?;
    } else {
        reader.read_exact(&mut packet[4..]).await?;
    }
    packet.truncate(raw);
    Ok(packet)
}
pub async fn write_packet<W: AsyncWrite + Unpin>(writer: &mut W, packet: &[u8]) -> io::Result<()> {
    writer.write_all(&stream_packet(packet)?).await
}

#[cfg(test)]
mod tests {
    use super::*;
    fn stun() -> Vec<u8> {
        let mut packet = vec![0; 20];
        packet[1] = 1;
        packet[4..8].copy_from_slice(&[0x21, 0x12, 0xa4, 0x42]);
        packet
    }
    #[tokio::test]
    async fn fragmented_and_coalesced_real_tcp_preserves_stun_and_channel_boundaries() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let task = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            assert_eq!(read_packet(&mut socket).await.unwrap(), stun());
            assert_eq!(
                read_packet(&mut socket).await.unwrap(),
                [0x40, 1, 0, 3, 1, 2, 3]
            );
            assert_eq!(read_packet(&mut socket).await.unwrap(), stun());
            write_packet(&mut socket, &[0x40, 1, 0, 1, 9])
                .await
                .unwrap();
        });
        let mut client = tokio::net::TcpStream::connect(address).await.unwrap();
        let mut stream = stream_packet(&stun()).unwrap();
        stream.extend(stream_packet(&[0x40, 1, 0, 3, 1, 2, 3]).unwrap());
        stream.extend(stream_packet(&stun()).unwrap());
        for chunk in stream.chunks(3) {
            client.write_all(chunk).await.unwrap();
            tokio::task::yield_now().await;
        }
        assert_eq!(read_packet(&mut client).await.unwrap(), [0x40, 1, 0, 1, 9]);
        task.await.unwrap();
    }
    #[tokio::test]
    async fn truncation_bad_cookie_reserved_channels_and_oversize_are_rejected() {
        for bytes in [
            vec![0],
            vec![0, 1, 0, 4],
            vec![0x40, 1, 0, 3, 1],
            vec![0x50, 0, 0, 0],
            vec![0x40, 0, 255, 255],
        ] {
            assert!(read_packet(&mut bytes.as_slice()).await.is_err());
        }
        let mut bad = stun();
        bad[4] = 0;
        assert!(stream_packet(&bad).is_err());
        let mut extra = stun();
        extra.push(1);
        assert!(stream_packet(&extra).is_err());
        assert_eq!(
            stream_packet(&[0x40, 0, 0, 1, 2]).unwrap(),
            [0x40, 0, 0, 1, 2, 0, 0, 0]
        );
    }
    #[test]
    fn arbitrary_small_packets_do_not_panic() {
        for size in 0..80 {
            for byte in 0..=255 {
                let _ = stream_packet(&vec![byte; size]);
            }
        }
    }
}

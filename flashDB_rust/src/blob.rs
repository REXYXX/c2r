#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct SavedBlob {
    pub meta_addr: u32,
    pub addr: u32,
    pub len: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Blob {
    data: Vec<u8>,
    pub saved: SavedBlob,
}

impl Blob {
    pub fn new(data: impl AsRef<[u8]>) -> Self {
        let data = data.as_ref().to_vec();
        Self {
            saved: SavedBlob {
                len: data.len(),
                ..SavedBlob::default()
            },
            data,
        }
    }

    pub fn empty() -> Self {
        Self::new([])
    }

    pub fn as_slice(&self) -> &[u8] {
        &self.data
    }

    pub fn len(&self) -> usize {
        self.data.len()
    }

    pub fn is_empty(&self) -> bool {
        self.data.is_empty()
    }

    pub fn read_into(&self, out: &mut [u8]) -> usize {
        let len = out.len().min(self.data.len());
        out[..len].copy_from_slice(&self.data[..len]);
        len
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct KvCacheNode {
    pub name_crc: u16,
    pub active: u16,
    pub addr: u32,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SectorCache<T> {
    entries: Vec<T>,
}

impl<T> SectorCache<T> {
    pub fn new() -> Self {
        Self { entries: Vec::new() }
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn push(&mut self, entry: T) {
        self.entries.push(entry);
    }

    pub fn entries(&self) -> &[T] {
        &self.entries
    }
}

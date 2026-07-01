use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileStorage {
    root: PathBuf,
}

impl FileStorage {
    pub fn new(root: impl AsRef<Path>) -> Self {
        Self {
            root: root.as_ref().to_path_buf(),
        }
    }

    pub fn path(&self) -> &Path {
        &self.root
    }

    pub fn read_all(&self) -> std::io::Result<Vec<u8>> {
        let mut out = Vec::new();
        fs::File::open(&self.root)?.read_to_end(&mut out)?;
        Ok(out)
    }

    pub fn write_all(&self, bytes: &[u8]) -> std::io::Result<()> {
        if let Some(parent) = self.root.parent() {
            fs::create_dir_all(parent)?;
        }
        let tmp = self.root.with_extension("tmp");
        let mut file = fs::File::create(&tmp)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::rename(tmp, &self.root)?;
        Ok(())
    }

    pub fn read_at(&self, offset: u64, out: &mut [u8]) -> std::io::Result<usize> {
        let mut file = OpenOptions::new().read(true).open(&self.root)?;
        file.seek(SeekFrom::Start(offset))?;
        file.read(out)
    }

    pub fn write_at(&self, offset: u64, bytes: &[u8]) -> std::io::Result<()> {
        if let Some(parent) = self.root.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(&self.root)?;
        file.seek(SeekFrom::Start(offset))?;
        file.write_all(bytes)?;
        file.sync_all()
    }
}

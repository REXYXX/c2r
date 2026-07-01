use std::path::{Path, PathBuf};

use crate::types::{DbConfig, DbControl, DbKind};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DbCore {
    pub name: String,
    pub kind: DbKind,
    pub storage: Option<PathBuf>,
    pub config: DbConfig,
    pub init_ok: bool,
}

impl DbCore {
    pub fn new(name: impl Into<String>, kind: DbKind) -> Self {
        Self {
            name: name.into(),
            kind,
            storage: None,
            config: DbConfig::default(),
            init_ok: true,
        }
    }

    pub fn with_storage_file(mut self, path: impl AsRef<Path>) -> Self {
        self.storage = Some(path.as_ref().to_path_buf());
        self.config.file_mode = true;
        self
    }

    pub fn control(&mut self, command: DbControl) -> Option<u32> {
        match command {
            DbControl::SetSecSize(size) => {
                self.config.sec_size = size;
                None
            }
            DbControl::GetSecSize => Some(self.config.sec_size),
            DbControl::SetFileMode(enabled) => {
                self.config.file_mode = enabled;
                None
            }
            DbControl::SetMaxSize(size) => {
                self.config.max_size = size;
                None
            }
            DbControl::SetNotFormat(enabled) => {
                self.config.not_formatable = enabled;
                None
            }
            DbControl::SetRollover(_)
            | DbControl::GetRollover
            | DbControl::GetLastTime => None,
        }
    }
}

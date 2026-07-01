use std::fmt;

#[derive(Debug)]
pub enum FlashDbError {
    Io(std::io::Error),
    Corrupt(String),
    InvalidKey,
    SavedFull,
    InitFailed,
    UnsupportedControl(&'static str),
}

pub type FlashDbResult<T> = Result<T, FlashDbError>;

impl fmt::Display for FlashDbError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            FlashDbError::Io(err) => write!(f, "io error: {err}"),
            FlashDbError::Corrupt(msg) => write!(f, "corrupt database: {msg}"),
            FlashDbError::InvalidKey => write!(f, "key must not be empty"),
            FlashDbError::SavedFull => write!(f, "database storage is full"),
            FlashDbError::InitFailed => write!(f, "database initialization failed"),
            FlashDbError::UnsupportedControl(name) => write!(f, "unsupported control command: {name}"),
        }
    }
}

impl std::error::Error for FlashDbError {}

impl From<std::io::Error> for FlashDbError {
    fn from(value: std::io::Error) -> Self {
        FlashDbError::Io(value)
    }
}

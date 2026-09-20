CREATE DATABASE IF NOT EXISTS docuflow CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'docuflow'@'localhost' IDENTIFIED BY 'docuflow';
GRANT ALL PRIVILEGES ON docuflow.* TO 'docuflow'@'localhost';
FLUSH PRIVILEGES;


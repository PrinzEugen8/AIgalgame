const https = require('https');
const fs = require('fs');
const path = require('path');

const version = 'v20.18.3';
const root = path.resolve('E:/AIgalgame/tools');
const zipPath = path.join(root, 'node-v20.zip');
const extractDir = path.join(root, 'node-v20');
const url = `https://nodejs.org/dist/${version}/node-${version}-win-x64.zip`;

fs.mkdirSync(root, { recursive: true });

function download(targetUrl, dest, redirects = 0) {
  if (redirects > 5) {
    throw new Error('Too many redirects');
  }

  https.get(targetUrl, (response) => {
    if (response.statusCode === 301 || response.statusCode === 302) {
      download(response.headers.location, dest, redirects + 1);
      return;
    }

    if (response.statusCode !== 200) {
      throw new Error(`HTTP ${response.statusCode} for ${targetUrl}`);
    }

    const file = fs.createWriteStream(dest);
    response.pipe(file);
    file.on('finish', () => file.close(() => console.log('downloaded')));
  }).on('error', (error) => {
    console.error(error);
    process.exit(1);
  });
}

download(url, zipPath);

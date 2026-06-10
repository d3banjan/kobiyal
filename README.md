# কবিয়াল

বাংলা কবিতার একটি মোবাইল-প্রথম, স্থির, মুক্ত-স্বত্ব পাঠঘর।

## কাজ চালানো

```bash
bun install
bun run dev
bun run build
```

## ডেটা নীতি

`src/data`-তে শুধু মুক্ত-স্বত্ব কবিদের তথ্য থাকে। কাজী নজরুল ইসলাম ও জসীমউদ্‌দীন ইচ্ছাকৃতভাবে বাদ।

এককালীন ইনজেস্ট স্ক্রিপ্টটি ডিফল্টভাবে `/scratch/Bengali-Poem-Dataset` থেকে পড়ে:

```bash
DATASET_ROOT=/scratch/Bengali-Poem-Dataset bun run ingest
```

স্ক্রিপ্টটি allowlist ছাড়া কোনো কবি পড়ে না, GPL-licensed source code কপি করে না, এবং সব poem JSON-এ `verified: false` রাখে।

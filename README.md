# Bandhani Studio

Django admin and Celery pipeline for generating, checking, and packaging Bandhani campaign images.

## Project Idea

Bandhani Studio helps a textile brand turn photographs of its real, handmade garments into campaign-ready fashion imagery. Text-only image prompts cannot reliably reproduce a particular garment's tie-dye pattern, colors, and motif placement. The pipeline therefore uses the uploaded flat-lay photograph as a visual reference throughout generation and quality control.

The goal is to automate the repetitive production work without automating the brand's final judgment. A staff member maintains product details and uploads garment photos in Django admin. From that upload onward, background workers generate and inspect candidate images, create ad formats, and notify a person when a campaign is ready to review. The system does not publish or post campaigns to social media.

### Upload-to-Review Flow

1. A newly saved product photo of kind `flatlay` triggers a Django signal. The signal gets or creates that product's campaign and queues a Celery task after the database transaction commits; it does not call image services or run image processing in the web request.
2. The worker builds an auditable prompt from the product pattern, approved color palette, occasion, and optional brief. It attaches the actual flat-lay photo and instructs the model to retain the garment's pattern, colors, motif layout, and handmade texture.
3. An image provider creates a candidate. Each business attempt is recorded as a separate `Generation`, including the exact prompt, provider/model, image, attempt number, and status.
4. Quality control runs two independent checks. A local Pillow/numpy palette comparison scores color similarity; a configurable vision provider assesses pattern structure, border and scatter-dot zones, and whether the Bandhani texture looks organically hand-tied rather than uniformly printed. Both checks must pass.
5. A passing image is packaged into square (`1:1`), portrait (`4:5`), and story (`9:16`) crops with a caption built from validated product data. The campaign moves to review and an email notification is sent.
6. A QC failure starts a new generation attempt with feedback derived from the recorded failure notes. There are at most three business attempts. If all fail, the campaign remains in the review queue with its full attempt history and a human-review notification; it is never silently approved.

Transient service failures are separate from QC failures: Celery retries the same attempt for temporary API/network errors, while a QC failure creates a new attempt and a corrected prompt. Per-campaign paid-generation limits stop runaway API costs, and exhausted or permanent task failures are surfaced as failed campaign status and an admin notification.

### Main Records

- `Product` stores the garment name, Bandhani pattern type, approved structured colors, and description.
- `ProductPhoto` stores flat-lay reference images and optional detail photos.
- `Campaign` connects a product to an occasion and optional creative brief. Upload-triggered campaigns default to `Everyday`; staff can edit campaign details in admin.
- `Generation` preserves each prompt, generated image, provider, QC score and notes, status, and attempt history.
- `AdAsset` stores each packaged crop and its caption. A generation can have one asset per supported aspect ratio.

### Runtime Boundaries

Django admin is the staff interface. PostgreSQL stores the workflow and audit data; Redis brokers Celery work; the `web` and `worker` containers share the mounted project media directory. Image generation and structural QC use provider interfaces so local development and CI can use zero-cost mock providers while production can select Gemini through environment variables. Provider/model selection is fixed when Django starts, and secrets are read from environment configuration rather than source code.

The pipeline is designed to tolerate duplicate task delivery: attempt creation, generation ownership, asset creation, and campaign notifications are guarded by database state. Human approval in admin is the final workflow step; approval marks the campaign done but does not trigger external publishing.

## Local Setup

The `.env` file is ignored by Git and may already contain Gemini credentials. If it exists, merge the values from `.env.example` into it; do not replace it and lose existing API keys. At minimum, set a unique random `SECRET_KEY` (50+ characters), a `POSTGRES_PASSWORD`, and `DEBUG=true` for local HTTP development. Keep `.env` private.

The default local configuration uses `IMAGE_PROVIDER=mock` and `QC_PROVIDER=mock`, so tests and uploads do not call paid APIs. Email is printed to the console by default. Set `ADMIN_EMAILS` to choose the notification recipient.

Start the services and apply the database schema:

```powershell
docker-compose up --build -d
docker-compose run --rm web python manage.py migrate
docker-compose run --rm web python manage.py createsuperuser
```

Open the admin at <http://localhost:8000/admin/>. Add a product and upload a flat-lay photo; the pipeline queues automatically. Inspect task activity with:

```powershell
docker-compose logs -f worker
```

Run the automated tests with:

```powershell
docker-compose run --rm web python manage.py test studio
```

## Gemini Providers

To enable paid image generation and vision QC, configure `GEMINI_API_KEY`, `GEMINI_IMAGE_MODEL`, and `GEMINI_QC_MODEL` in `.env`, then set:

```dotenv
IMAGE_PROVIDER=gemini
QC_PROVIDER=gemini
```

A consumer Gemini subscription does not provide developer API billing. Confirm Google Cloud API billing and quotas before enabling these providers. `MAX_PAID_CALLS_PER_CAMPAIGN` limits paid image-generation calls per campaign.

## Production

Set `DEBUG=false`, a unique `SECRET_KEY`, and explicit `ALLOWED_HOSTS`. Production mode enables HTTPS redirects, secure cookies, and HSTS by default, so serve the application behind HTTPS. Configure SMTP environment variables and `ADMIN_EMAILS` for real campaign notifications. Never commit `.env` or provider credentials.

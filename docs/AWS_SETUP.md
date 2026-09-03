# AWS Account Setup (do this yourself — see note below)

This is a one-time setup you do in your own browser/terminal. Nothing here
should ever be typed into a chat with an AI assistant, including this one —
an IAM access key is a credential, exactly like a password, and pasting one
into a chat is the same risk as pasting a password into a chat.

## 1. Don't use the root account for daily work

You already have the AWS account (root login: your email + the password you
signed up with). Keep that login for account-level tasks only (billing,
closing the account, etc.) and create a separate IAM user for everything
else.

1. Sign in to the [AWS Console](https://console.aws.amazon.com/) with your
   root email/password.
2. Go to **IAM** → **Users** → **Create user**.
3. User name: something like `dev-cli` or your own name.
4. **Do not** check "Provide user access to the AWS Management Console"
   unless you also want to log in via the web UI with this user — for CLI/CDK
   work you don't need it.
5. **Permissions**: for this project's scope, attaching the AWS-managed
   policy `AdministratorAccess` is the pragmatic choice while you're
   learning and iterating across many services (Lambda, API Gateway,
   DynamoDB, S3, Step Functions, Cognito, Bedrock, IAM itself for creating
   execution roles). It is broad — if you want tighter scoping later, the
   individual per-phase IAM permissions needed are listed in
   [`AWS_ROADMAP.md`](AWS_ROADMAP.md) and can be swapped in once the shape
   of what you need has stabilized. Do not use root credentials for any of
   the CDK/CLI work below regardless of which policy you pick.
6. Finish creating the user.

## 2. Create an access key for CLI use

1. Open the new user → **Security credentials** tab → **Access keys** →
   **Create access key**.
2. Choose **Command Line Interface (CLI)** as the use case, acknowledge the
   recommendation prompt, create it.
3. You'll see an **Access key ID** and a **Secret access key**. The secret
   is shown **exactly once** — download the `.csv` AWS offers, or copy both
   values somewhere safe (a password manager, not a chat window) right now.

## 3. Configure the AWS CLI locally

In your own terminal (not through any AI assistant):

```bash
aws configure --profile dev
```

It will prompt for four things, one at a time:

```
AWS Access Key ID [None]: <paste the access key ID>
AWS Secret Access Key [None]: <paste the secret access key>
Default region name [None]: us-east-1
Default output format [None]: json
```

(`us-east-1` is a reasonable default — it has the broadest service
availability, including Bedrock model access, and is the region this
project's CDK stacks default to unless you change it. Pick a different
region if you have a reason to.)

This writes `~/.aws/credentials` and `~/.aws/config` — nothing leaves your
machine from this step.

## 4. Make the profile the default for this project

Two options, pick one:

- **Environment variable per shell session** (safest, no persistent state):
  ```bash
  export AWS_PROFILE=dev
  ```
  Add that line to your shell's `~/.zshrc` if you want it to persist across
  terminal sessions.
- **Named profile per command**, no environment variable needed:
  ```bash
  aws sts get-caller-identity --profile dev
  cdk deploy --profile dev ...
  ```

## 5. Verify it worked

```bash
aws sts get-caller-identity
```

Should print your account ID, and an ARN ending in
`user/dev-cli` (or whatever you named the user) — **not** `root`. If you see
`root` in the ARN, something's misconfigured; stop and re-check step 3.

## 6. Bootstrap the CDK (needed once per account+region)

Once the above is verified, from `infra/`:

```bash
cd infra
cdk bootstrap
```

This creates a small set of CDK-owned resources (an S3 bucket for deployment
assets, IAM roles) in your account — it's a one-time, low/no-cost step, and
is the last thing needed before `cdk deploy` will work.

## What to tell your assistant afterward

Just confirm credentials are configured (`aws sts get-caller-identity`
succeeds) — do not paste the actual key values, the account ID, or the ARN
into chat unless you're comfortable with that information being visible
there (the account ID alone is not a secret, but there's no need to share
it). From there, CDK deploys can proceed.

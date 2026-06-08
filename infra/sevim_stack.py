"""SevimStack — single CDK stack for the public Sevim deployment.

Provisions, in dependency order:

  1. **VPC** with 2 public + 2 private (with NAT) subnets in 2 AZs.
  2. **S3 buckets** for canvas WAVs, training data, LoRA artefacts.
  3. **RDS Postgres** (db.t4g.small, single AZ for cost) in private subnets;
     credentials live in a Secrets Manager secret CDK creates automatically.
  4. **Secrets Manager** entries for OpenAI key, auth secret, IP-hash salt,
     SES sender address.  Values are placeholders until you fill them with
     ``aws secretsmanager put-secret-value``.
  5. **ECR repo** for the Sevim image, and a CDK ``DockerImageAsset`` that
     builds the image from ``../Dockerfile`` and pushes it.
  6. **ECS Fargate** service: 1 task (1 vCPU / 2 GB) behind an ALB.  Task
     env vars include the SEVIM_* knobs; secrets are injected via the
     ``Secret`` ECS construct so the values never appear in plaintext on
     the task definition.
  7. **(Optional) ACM cert + Route 53 A-alias** when ``SEVIM_DOMAIN`` is
     passed.  Cert validation uses DNS records the stack adds.
  8. **CloudWatch log group** for task logs (14-day retention).

Outputs:

  * ``ServiceUrl``         — ALB DNS or domain, the user-visible URL.
  * ``DbSecretArn``        — RDS-managed secret (already wired into the task).
  * ``OpenAiSecretArn``    — secret to populate manually with your key.
  * ``CanvasBucketName``   — S3 bucket for canvas WAVs.
  * ``LoraBucketName``     — S3 bucket for LoRA artefacts.
  * ``TrainingBucketName`` — S3 bucket for distillation training data.
"""
from __future__ import annotations

import os
from typing import Optional

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_certificatemanager as acm,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_rds as rds,
    aws_route53 as route53,
    aws_route53_targets as r53_targets,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class SevimStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain: Optional[str] = None,
        domain_zone_id: Optional[str] = None,
        redirect_domains: Optional[list[str]] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── 1. Network ────────────────────────────────────────────────
        # 2 AZ × (public + private-with-NAT) — minimum for ALB.  One NAT
        # gateway is enough for outbound traffic (saves $32/mo vs 2).
        vpc = ec2.Vpc(
            self, "Vpc",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24,
                ),
            ],
        )

        # ── 2. S3 buckets ────────────────────────────────────────────
        # Three buckets — privacy by default; lifecycle rules keep
        # storage costs predictable as the corpus grows.
        canvas_bucket = s3.Bucket(
            self, "CanvasBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,  # don't lose user output
            versioned=False,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-canvas-wavs-90d",
                    expiration=Duration.days(90),
                    enabled=True,
                ),
            ],
        )
        training_bucket = s3.Bucket(
            self, "TrainingBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
        )
        lora_bucket = s3.Bucket(
            self, "LoraBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
            versioned=True,  # so we can roll back a bad LoRA
        )

        # ── 3. RDS Postgres ──────────────────────────────────────────
        db_creds = rds.Credentials.from_generated_secret(
            username="sevim",
            secret_name="sevim/db_credentials",
        )
        db = rds.DatabaseInstance(
            self, "TelemetryDb",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16,
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE4_GRAVITON,
                ec2.InstanceSize.SMALL,
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
            ),
            credentials=db_creds,
            allocated_storage=20,
            max_allocated_storage=100,
            backup_retention=Duration.days(7),
            deletion_protection=False,  # flip to True after first prod cut
            publicly_accessible=False,
            removal_policy=RemovalPolicy.SNAPSHOT,
            multi_az=False,
            database_name="sevim",
        )

        # ── 4. App-side secrets ───────────────────────────────────────
        # We pre-create empty placeholders so the ECS task can mount them
        # at boot.  Populate after deploy with:
        #   aws secretsmanager put-secret-value \
        #       --secret-id sevim/openai --secret-string sk-...
        openai_secret = secretsmanager.Secret(
            self, "OpenAiKey",
            secret_name="sevim/openai",
            description="OpenAI API key — populate after deploy",
        )
        # Auto-generated 64-byte HMAC secret for magic-link tokens + cookies.
        auth_secret = secretsmanager.Secret(
            self, "AuthSecret",
            secret_name="sevim/auth_secret",
            description="HMAC key for magic-link tokens and login cookies",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                exclude_punctuation=True,
                password_length=64,
            ),
        )
        # Auto-generated IP-hash salt — stable across deploys (regenerating
        # would invalidate every existing IP rate-limit bucket).
        ip_hash_salt = secretsmanager.Secret(
            self, "IpHashSalt",
            secret_name="sevim/ip_hash_salt",
            description="Salt for sha256(IP) telemetry hashing",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                exclude_punctuation=True,
                password_length=32,
            ),
        )
        ses_from = secretsmanager.Secret(
            self, "SesFromAddress",
            secret_name="sevim/ses_from",
            description="Verified SES sender address (e.g. 'Sevim <noreply@…>')",
        )

        # ── 5. Container image ───────────────────────────────────────
        # CDK builds the Docker image locally (needs `docker` on the
        # synth host) and pushes to a CDK-managed ECR repo.  For
        # CodeBuild-based remote builds, swap to a CdkPipeline.
        # Pass the deploy-time git SHA via the Dockerfile's
        # SEVIM_GIT_SHA build-arg.  This stamps every captured
        # /studio/feedback row with the deploy it came from, so the
        # admin recurrence-after-fix heuristic has a basis to compare.
        sevim_git_sha = (os.environ.get("SEVIM_GIT_SHA")
                         or "unknown")[:40]
        image_asset = ecr_assets.DockerImageAsset(
            self, "SevimImage",
            directory="..",  # repo root — Dockerfile lives there
            file="Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
            build_args={"SEVIM_GIT_SHA": sevim_git_sha},
        )

        # ── 6. ECS Fargate behind ALB ────────────────────────────────
        log_group = logs.LogGroup(
            self, "AppLogs",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, container_insights=True)

        env_vars: dict[str, str] = {
            "SEVIM_TELEMETRY": "1",
            "SEVIM_RATE_LIMIT": "1",
            "SEVIM_COST_GUARD": "1",
            "SEVIM_COST_DAILY_MAX_USD": "10.00",  # raised from default $1
            "SEVIM_CONTACT_TO": "gradersystem@gmail.com",
            "SEVIM_CONTENT_FILTER": "1",
            "SEVIM_TRUST_PROXY": "1",
            # Magic-link sign-in stays ON: it's a low-friction,
            # email-only round-trip (no password, no profile page),
            # and the captured email is what lets us count distinct
            # people in telemetry.
            "SEVIM_AUTH_REQUIRED": "1",
            "SEVIM_VLLM_URL": "https://api.openai.com/v1",
            "SEVIM_VLLM_MODEL": "gpt-4o",
            # Deploy-time override of the admin's active-model choice.
            # Without this, resolve_backend() respects whatever was set
            # via /studio/admin (which had drifted to gpt-4o-mini and
            # produced text-only figures with bad arithmetic).  Setting
            # it here forces every Fargate task to use gpt-4o until
            # this line is removed.  Unset (next cdk deploy with the
            # line removed) to release the override.
            "SEVIM_FORCE_ACTIVE_MODEL": "gpt-4o",
            # Figure-review backend (math-correctness inspector).
            # Vision-mode on gpt-4o so the reviewer rasterises the
            # SVG to PNG and inspects the pixels — only way to catch
            # text overlap and wrong-topic figures.  Text-mode review
            # missed real overlap bugs because SVG XML doesn't render.
            # Adds ~10-20 s per turn to the worst-case retry path but
            # the figures that actually pass are dramatically better.
            "SEVIM_REVIEW_MODE": "vision",
            "SEVIM_REVIEW_MODEL": "gpt-4o",
            # TTS: tts-1 is ~3x faster than tts-1-hd at virtually
            # identical audibility for short narration phrases.  At
            # 15-30 phrases per figure, hd was adding 20-40s of
            # wall-clock to the turn — long enough that learners gave
            # up on the canvas before it loaded.  Quality knob:
            # bump back to "tts-1-hd" if/when speed isn't the bind.
            "SEVIM_TTS_MODEL": "tts-1",
            # Admin dashboard (/studio/admin) is gated by an email
            # whitelist; only signed-in users whose magic-link cookie
            # carries one of these e-mails can see it.  Everyone else
            # gets a 404, so the route is effectively invisible.
            "SEVIM_ADMIN_EMAILS": "arash_kermani@yahoo.com",
            "SEVIM_STORAGE_URL": f"s3://{canvas_bucket.bucket_name}",
            "SEVIM_EXPORT_S3_BUCKET": training_bucket.bucket_name,
            "SEVIM_LORA_S3_BUCKET": lora_bucket.bucket_name,
            "AWS_REGION": self.region,
        }
        if domain:
            env_vars["SEVIM_AUTH_DOMAIN"] = domain

        secrets_map: dict[str, ecs.Secret] = {
            "OPENAI_API_KEY":          ecs.Secret.from_secrets_manager(openai_secret),
            "SEVIM_AUTH_SECRET":       ecs.Secret.from_secrets_manager(auth_secret),
            "SEVIM_IP_HASH_SALT":      ecs.Secret.from_secrets_manager(ip_hash_salt),
            "SEVIM_SES_FROM_ADDRESS":  ecs.Secret.from_secrets_manager(ses_from),
            # RDS-managed secret is a JSON dict
            # ({username,password,host,port,dbname,...}) — inject it as
            # SEVIM_DB_SECRET_JSON and let service/secrets.py assemble
            # the postgresql:// URL at boot.  Keeps the CFN template
            # free of any string-templating across resources.
            "SEVIM_DB_SECRET_JSON":    ecs.Secret.from_secrets_manager(
                db.secret,  # type: ignore[arg-type]
            ),
        }

        cert: Optional[acm.ICertificate] = None
        zone: Optional[route53.IHostedZone] = None
        if domain:
            if domain_zone_id:
                zone = route53.HostedZone.from_hosted_zone_attributes(
                    self, "Zone",
                    hosted_zone_id=domain_zone_id,
                    zone_name=domain,
                )
            else:
                zone = route53.HostedZone.from_lookup(
                    self, "Zone", domain_name=domain,
                )
            cert = acm.Certificate(
                self, "Cert",
                domain_name=domain,
                validation=acm.CertificateValidation.from_dns(zone),
            )

        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, "Service",
            cluster=cluster,
            cpu=1024,
            # Bumped 2048 -> 3072: a single task was OOM-killed (exit
            # 137) after ~26 h of uptime — a slow memory climb plus a
            # piper TTS re-synth spike pushed it past the 2 GB hard
            # limit and ECS had to reschedule, blanking the site until
            # the replacement went healthy.  3 GB buys headroom while
            # the underlying leak is profiled separately.
            memory_limit_mib=3072,
            # Run TWO tasks so a single OOM (or a rolling deploy) never
            # takes the whole site down — the ALB keeps serving from the
            # healthy task while ECS replaces the dead one.  Staggered
            # start times mean a slow leak won't kill both at once.
            desired_count=2,
            public_load_balancer=True,
            assign_public_ip=False,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_docker_image_asset(image_asset),
                container_port=8080,
                environment=env_vars,
                secrets=secrets_map,
                log_driver=ecs.LogDrivers.aws_logs(
                    stream_prefix="sevim",
                    log_group=log_group,
                ),
            ),
            certificate=cert,
            # ELBSecurityPolicy-2016-08 (the CDK default) caps the listener at
            # TLS 1.0–1.2 and refuses TLS 1.3.  Some corporate / school proxies
            # (esp. Canadian education and gov networks running Zscaler /
            # Fortinet / Palo Alto) flag TLS-1.0/1.1-capable endpoints as
            # insecure and block the page with a generic "security concerns"
            # warning.  RECOMMENDED_TLS resolves to
            # ELBSecurityPolicy-TLS13-1-2-2021-06: TLS 1.2 minimum, TLS 1.3
            # enabled, no TLS 1.0/1.1.
            ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS,
            domain_name=domain,
            domain_zone=zone,
            redirect_http=bool(domain),  # only when we have HTTPS
            health_check_grace_period=Duration.seconds(60),
            # Roll back automatically if the new task version doesn't
            # become healthy.  Cuts failed-deployment timeout from 3 h
            # to ~10 min.
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )
        service.target_group.configure_health_check(
            path="/health",
            healthy_http_codes="200",
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
            interval=Duration.seconds(30),
            timeout=Duration.seconds(5),
        )
        # With desired_count=2 a client could be round-robined to the
        # sibling task mid-generation, before the new canvas has been
        # flushed to S3 (REGISTRY.get rehydrates from S3 only AFTER the
        # first write).  Pin each client to one task with LB cookie
        # stickiness so the /chat, /state, /events and /narration.wav
        # calls of a single turn all land on the task that is building
        # the canvas.  Persisted canvases still rehydrate cross-task on
        # a miss, so stickiness is a hot-path optimisation, not a
        # correctness crutch.
        service.target_group.enable_cookie_stickiness(Duration.hours(8))

        # Grant the task role least-privileged S3 access.
        canvas_bucket.grant_read_write(service.task_definition.task_role)
        training_bucket.grant_read_write(service.task_definition.task_role)
        lora_bucket.grant_read(service.task_definition.task_role)

        # SES send permission for magic-link delivery.  Scoped to the
        # account default — narrow further to a specific verified
        # identity if you operate multiple senders.
        service.task_definition.task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["ses:SendEmail", "ses:SendRawEmail"],
                resources=["*"],
            )
        )

        # RDS connectivity: open Postgres port from the Fargate SG only.
        db.connections.allow_default_port_from(service.service)

        # ── 7b. Typo / alias domains that 301 to the canonical one ─────
        # Each entry gets its own ACM cert (DNS-validated against its
        # own hosted zone), an A-alias to the ALB, and a listener rule
        # that 301-redirects by Host header.  Costs $15/yr per domain
        # for registration; nothing extra for the ACM cert or rule.
        if domain and redirect_domains:
            listener = service.listener
            for i, rdomain in enumerate(redirect_domains):
                rzone = route53.HostedZone.from_lookup(
                    self, f"RedirectZone{i}", domain_name=rdomain,
                )
                rcert = acm.Certificate(
                    self, f"RedirectCert{i}",
                    domain_name=rdomain,
                    validation=acm.CertificateValidation.from_dns(rzone),
                )
                listener.add_certificates(
                    f"RedirectListenerCert{i}", certificates=[rcert],
                )
                route53.ARecord(
                    self, f"RedirectAlias{i}",
                    zone=rzone,
                    record_name=rdomain,
                    target=route53.RecordTarget.from_alias(
                        r53_targets.LoadBalancerTarget(service.load_balancer),
                    ),
                )
                listener.add_action(
                    f"RedirectAction{i}",
                    priority=10 + i,
                    conditions=[elbv2.ListenerCondition.host_headers([rdomain])],
                    action=elbv2.ListenerAction.redirect(
                        host=domain,
                        port="443",
                        protocol="HTTPS",
                        permanent=True,  # 301
                    ),
                )

        # ── 7b'. www subdomain — same hosted zone as the apex domain ──
        # Adds www.<apex> as a 301-redirect alias to the apex.  Distinct
        # from 7b above because the www record lives inside the SAME
        # hosted zone as the apex (which we already looked up), so we
        # don't need a fresh HostedZone.from_lookup call.  A new ACM
        # cert is issued just for www (DNS-validated against the apex
        # zone), attached as an additional certificate on the existing
        # HTTPS listener, and a Host-header listener rule 301-redirects
        # to the apex.  Non-destructive: leaves the apex cert and
        # routing untouched.
        if domain and zone:
            www_name = f"www.{domain}"
            www_cert = acm.Certificate(
                self, "WwwCert",
                domain_name=www_name,
                validation=acm.CertificateValidation.from_dns(zone),
            )
            service.listener.add_certificates(
                "WwwListenerCert", certificates=[www_cert],
            )
            route53.ARecord(
                self, "WwwAlias",
                zone=zone,
                record_name="www",  # relative to the zone => www.<apex>
                target=route53.RecordTarget.from_alias(
                    r53_targets.LoadBalancerTarget(service.load_balancer),
                ),
            )
            service.listener.add_action(
                "WwwRedirectAction",
                # Priority must be UNIQUE across all listener rules; the
                # 7b loop uses 10..10+len(redirect_domains)-1, so we
                # pick a higher value to avoid colliding even if more
                # typo redirects get added later.
                priority=50,
                conditions=[
                    elbv2.ListenerCondition.host_headers([www_name]),
                ],
                action=elbv2.ListenerAction.redirect(
                    host=domain,
                    port="443",
                    protocol="HTTPS",
                    permanent=True,  # 301
                ),
            )

        # ── 7c. (Optional) Qwen vLLM GPU instance ────────────────────
        # Spins up a g6.xlarge (NVIDIA L4, 24 GB) running vLLM with
        # --enable-lora, preloaded with qwen_lora_v4 from the LoRA S3
        # bucket.  Exposes the OpenAI-compatible chat-completions API
        # on the private VPC at port 8000.  Fargate's env var
        # SEVIM_QWEN_VLLM_URL is wired to its private DNS name.
        #
        # Opt-in: deploy with ``cdk deploy -c enable_qwen=1`` to spin
        # this up.  Default off — bare provision adds ~$0.30/hr (spot)
        # or ~$0.80/hr (on-demand) every hour the stack is alive.
        enable_qwen = bool(self.node.try_get_context("enable_qwen"))
        if enable_qwen:
            qwen_sg = ec2.SecurityGroup(
                self, "QwenVllmSg",
                vpc=vpc,
                # AWS EC2 SG descriptions must be ASCII only --- em-dashes
                # and other Unicode characters fail with an InvalidRequest.
                description="Qwen vLLM serving SG - inbound 8000 from Fargate only",
                allow_all_outbound=True,
            )
            # Only Fargate tasks may hit the vLLM port.
            qwen_sg.add_ingress_rule(
                peer=service.service.connections.security_groups[0],
                connection=ec2.Port.tcp(8000),
                # SG rule descriptions accept a tight character set:
                # a-zA-Z0-9. _-:/()#,@[]+=&;{}!$*  (no arrows allowed)
                description="Studio Fargate to Qwen vLLM port 8000",
            )

            qwen_role = iam.Role(
                self, "QwenInstanceRole",
                assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "AmazonSSMManagedInstanceCore"),
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "CloudWatchAgentServerPolicy"),
                ],
            )
            lora_bucket.grant_read(qwen_role)

            # User-data: install vLLM, pull the LoRA from S3, run vLLM
            # as a systemd unit.  AWS Deep Learning Base GPU AMI ships
            # with the NVIDIA driver + CUDA toolkit + Python 3.11
            # preinstalled — no driver dance required.
            user_data = ec2.UserData.for_linux()
            user_data.add_commands(
                "set -eux",
                "exec > >(tee /var/log/sevim-bootstrap.log) 2>&1",
                # The DLAMI base ships with python3.10; pin to the
                # plain python3-venv package so we don't fight whichever
                # interpreter version the next AMI rev decides to ship.
                "apt-get update -y && apt-get install -y python3-venv python3-pip awscli",
                "python3 -m venv /opt/vllm",
                "/opt/vllm/bin/pip install --upgrade pip",
                # Pin vLLM to a version known to support Qwen2.5 + LoRA.
                "/opt/vllm/bin/pip install 'vllm==0.6.6.post1' huggingface_hub",
                # Pull the adapter — bucket name interpolated from CDK.
                "mkdir -p /opt/loras",
                f"aws s3 sync s3://{lora_bucket.bucket_name}/qwen_lora_v4/ "
                "/opt/loras/qwen_lora_v4/",
                # Systemd unit.
                "cat >/etc/systemd/system/vllm.service <<'UNIT'",
                "[Unit]",
                "Description=vLLM Qwen2.5-7B + Khayyam Math LoRA",
                "After=network-online.target",
                "Wants=network-online.target",
                "",
                "[Service]",
                "Type=simple",
                "ExecStart=/opt/vllm/bin/python -m vllm.entrypoints.openai.api_server "
                "  --model Qwen/Qwen2.5-7B-Instruct "
                "  --host 0.0.0.0 --port 8000 "
                "  --enable-lora "
                "  --lora-modules qwen_lora_v4=/opt/loras/qwen_lora_v4 "
                "  --max-model-len 6144 "
                "  --dtype bfloat16",
                "Restart=on-failure",
                "RestartSec=10",
                "",
                "[Install]",
                "WantedBy=multi-user.target",
                "UNIT",
                "systemctl daemon-reload",
                "systemctl enable vllm",
                "systemctl start vllm",
            )

            # AMI: use the AWS-published "Deep Learning Base OSS Nvidia
            # Driver GPU AMI (Ubuntu 22.04)" via its SSM parameter — the
            # parameter always resolves to the latest published AMI ID,
            # so we don't have to bump a date string each release.
            qwen_ami = ec2.MachineImage.from_ssm_parameter(
                "/aws/service/deeplearning/ami/x86_64/"
                "base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id",
                os=ec2.OperatingSystemType.LINUX,
            )

            qwen_instance = ec2.Instance(
                self, "QwenVllmInstance",
                instance_type=ec2.InstanceType("g6.xlarge"),
                machine_image=qwen_ami,
                vpc=vpc,
                vpc_subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                security_group=qwen_sg,
                role=qwen_role,
                user_data=user_data,
                block_devices=[
                    ec2.BlockDevice(
                        device_name="/dev/sda1",
                        volume=ec2.BlockDeviceVolume.ebs(
                            volume_size=100,
                            volume_type=ec2.EbsDeviceVolumeType.GP3,
                            delete_on_termination=True,
                        ),
                    ),
                ],
            )
            # On-demand pricing.  CloudFormation's bare AWS::EC2::Instance
            # resource does not accept InstanceMarketOptions (that
            # property only exists on Launch Templates), so the spot
            # path requires an ASG-of-one + LaunchTemplate.  For the
            # initial deploy we keep the simpler Instance construct on
            # on-demand pricing (~$0.80/hr g6.xlarge in us-east-1) and
            # migrate to spot once the path is proven; the Qwen route
            # is gracefully degradable so the upgrade can roll any time.

            # Wire SEVIM_QWEN_VLLM_URL into the Fargate task env so the
            # studio dropdown's "Qwen" choice resolves to this instance.
            service.task_definition.default_container.add_environment(
                "SEVIM_QWEN_VLLM_URL",
                f"http://{qwen_instance.instance_private_ip}:8000/v1",
            )

            CfnOutput(self, "QwenInstancePrivateIp",
                      value=qwen_instance.instance_private_ip)
            CfnOutput(self, "QwenInstanceId",
                      value=qwen_instance.instance_id)

        # ── 7. Outputs ───────────────────────────────────────────────
        url = f"https://{domain}" if domain else f"http://{service.load_balancer.load_balancer_dns_name}"
        CfnOutput(self, "ServiceUrl", value=url)
        CfnOutput(self, "DbSecretArn", value=(db.secret.secret_arn if db.secret else "n/a"))
        CfnOutput(self, "OpenAiSecretArn", value=openai_secret.secret_arn)
        CfnOutput(self, "AuthSecretArn", value=auth_secret.secret_arn)
        CfnOutput(self, "CanvasBucketName", value=canvas_bucket.bucket_name)
        CfnOutput(self, "TrainingBucketName", value=training_bucket.bucket_name)
        CfnOutput(self, "LoraBucketName", value=lora_bucket.bucket_name)
        CfnOutput(self, "ImageUri", value=image_asset.image_uri)

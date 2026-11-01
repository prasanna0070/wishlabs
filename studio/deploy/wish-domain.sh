#!/bin/bash
# Serve the Wishwright Labs site (Cloud Run wishwright-labs) at https://wish.plugbook.si through the existing
# Plugbook load balancer (34.54.2.16). Cloud Run domain mappings do not exist in asia-south1, hence the LB.
# Additive only: plugbook.si, www and plugbook.quarktex.com routing is untouched.
# Claude's auto mode refuses load-balancer/production changes, so Prasanna runs:  ! bash studio/deploy/wish-domain.sh
# DNS (Hostinger hPanel, by hand): CNAME  wish  ->  plugbook.si   (TTL 300). The certificate turns ACTIVE 15-60 min after.
set -euo pipefail
P=your-gcp-project; R=asia-south1; HOST=wish.plugbook.si
SVC=wishwright-labs; NEG=$SVC-neg; BE=$SVC-be; CERT=wish-plugbook-si-cert
g() { gcloud "$@" --project=$P --quiet; }
has() { "$@" --project=$P >/dev/null 2>&1; }
has gcloud compute network-endpoint-groups describe $NEG --region=$R || \
  g compute network-endpoint-groups create $NEG --region=$R --network-endpoint-type=serverless --cloud-run-service=$SVC
has gcloud compute backend-services describe $BE --global || \
  g compute backend-services create $BE --global --load-balancing-scheme=EXTERNAL_MANAGED
gcloud compute backend-services describe $BE --global --project=$P --format='value(backends[].group)' | grep -q $NEG || \
  g compute backend-services add-backend $BE --global --network-endpoint-group=$NEG --network-endpoint-group-region=$R
has gcloud compute ssl-certificates describe $CERT --global || \
  g compute ssl-certificates create $CERT --global --domains=$HOST
CERTS=$(gcloud compute target-https-proxies describe plugbook-https-proxy --global --project=$P --format='value(sslCertificates)' \
  | tr ';' '\n' | sed 's#.*/##' | grep -v "^$CERT$" | paste -sd, -)
g compute target-https-proxies update plugbook-https-proxy --global --ssl-certificates="$CERTS,$CERT"
if ! gcloud compute url-maps describe plugbook-urlmap --global --project=$P --format=json | grep -q "\"$HOST\""; then
  g compute url-maps add-path-matcher plugbook-urlmap --global --path-matcher-name=wish \
    --default-service=$BE --new-hosts=$HOST
fi
echo "== proxy certs: $(gcloud compute target-https-proxies describe plugbook-https-proxy --global --project=$P --format='value(sslCertificates)' | tr ';' '\n' | sed 's#.*/##' | paste -sd' ' -)"
echo "== cert $CERT: $(gcloud compute ssl-certificates describe $CERT --global --project=$P --format='value(managed.status,managed.domainStatus)')"
echo "== DNS now: $(curl -s "https://dns.google/resolve?name=$HOST&type=A" | python3 -c 'import json,sys;print([a["data"] for a in json.load(sys.stdin).get("Answer",[])])')"
echo "If DNS is empty: hPanel > Domains > plugbook.si > DNS / Nameservers > add CNAME  wish -> plugbook.si  (TTL 300)."
echo "Then wait for the cert to show ACTIVE and open https://$HOST"

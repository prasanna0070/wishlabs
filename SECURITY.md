# Security

Please report a vulnerability privately: open the **Security** tab of this repository and choose
**Report a vulnerability**. Do not open a public issue for a security problem.

Useful details: what you found, how to reproduce it, and what an attacker could do with it. You will get a reply
as soon as possible, and credit in the fix if you want it.

In scope: the code in this repository (studio server and app, engine, builder, deploy scripts). The hosted studio
at wish.plugbook.si runs this code; please do not test against it in a way that affects other users or costs
money (for example by mass-creating builds).

Secrets never belong in this repository. The server reads its access key from the environment or from Google
Secret Manager.

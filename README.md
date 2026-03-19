<!-- ⚠️ This README has been generated from the file(s) "blueprint.md" ⚠️--><p align="center">
  <img src="https://static.wixstatic.com/media/09a6dd_eae6b87971dd4d14ba7792cdd237dd76~mv2.png" alt="Logo" width="300" height="auto" />
</p>
<p align="center">
<a href=""><img alt="Stars" src="https://img.shields.io/github/stars/devopspass/devopspass" height="20"/></a>
<a href="https://medium.com/@devopspass/"><img alt="Medium" src="https://img.shields.io/badge/Medium-12100E?style=for-the-badge&logo=medium&logoColor=white" height="20"/></a>
<a href="https://dev.to/devopspass"><img alt="dev.to" src="https://img.shields.io/badge/dev.to-0A0A0A?style=for-the-badge&logo=devdotto&logoColor=white" height="20"/></a>
<a href="https://www.linkedin.com/company/devopspass-ai"><img alt="LinkedIn" src="https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white" height="20"/></a>
<a href="https://www.youtube.com/@DevOpsPassAI"><img alt="YouTube" src="https://img.shields.io/badge/YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white" height="20"/></a>
<a href="https://twitter.com/devops_pass_ai"><img alt="Twitter" src="https://img.shields.io/badge/Twitter-1DA1F2?style=for-the-badge&logo=twitter&logoColor=white" height="20"/></a>
	</p>

<p align="center">
  <b>Interract with your entire DevOps Platform from a single place</b></br>
  <sub>Make DevOps-related activities one-click simple, without additional reading and searching.<sub>
</p>

<br />



[![-----------------------------------------------------](https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/water.png)](#-join-community)

## 💬 Join community

Join our Slack community, ask questions, contribute, get help!

[![Join Slack](https://img.shields.io/badge/Join%20our-Slack-4A154B?style=for-the-badge&logo=slack&logoColor=white)](https://join.slack.com/t/devops-pass-ai/shared_invite/zt-2gyn62v9f-5ORKktUINe43qJx7HtKFcw)


[![-----------------------------------------------------](https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/water.png)](#-screenshots)

## 📸 Screenshots

Example of Chat with DevOps Pass AI - Terraform pipeline analysis

![DOP v10 - example of Chat](https://raw.githubusercontent.com/devopspass/devopspass/refs/heads/main/images/screen1.png)

![DOP v10 - example of Chat](https://raw.githubusercontent.com/devopspass/devopspass/refs/heads/main/images/screen2.png)


[![-----------------------------------------------------](https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/water.png)](#-why-devops-pass-ai)

## ⭐️ Why DevOps Pass AI?

Look, its early demo of new version of DOP which is really using AI. It's pretty raw, BUT!

Right now it may help you, for example on new project to deep dive into Infrastructure, find out which products team is developing, which envs, where they are deployed, how monitored and so on.

It's more like "DevOps knowledge mining app" at the moment.

Right now there is no access to AWS or Kubernetes - frankly too dangerous.

Inside of container I'm not even installing `kubectl` or `aws-cli` and even after that AI sometimes trying to write its own tools in Python to make requests to Kubernetes or AWS (total facepalm...).

Before access to tools will not be fixed - NO ACCESS TO REAL INFRA!

Use it to dig knowledge, analyze your pipelines output, search for configs, etc.

## 📥 Installation

You'll need Docker Compose or Podman Compose installed.

```
git clone https://github.com/devopspass/devopspass.git
cd devopspass/
```

Edit `docker-compose.yml` to provide volume for `/workspace/git` to be able to open repos from UI (may be configured later).

```
docker compose up -d
```

And go to http://localhost:10818/

Enjoy...see you in my Slack (link at the top)!
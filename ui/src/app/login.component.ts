import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { AuthService } from './auth.service';

@Component({
  selector: 'dop-login',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './login.component.html',
  styleUrl: './login.component.css',
})
export class LoginComponent implements OnInit {
  email = '';
  acceptedRisk = false;
  loading = false;
  sentTo = '';
  error = '';
  redirectPath = '/';

  constructor(
    private readonly auth: AuthService,
    private readonly route: ActivatedRoute,
    private readonly router: Router,
  ) {}

  async ngOnInit(): Promise<void> {
    await this.auth.waitForAuthReady();
    if (this.auth.isLoggedIn()) {
      await this.router.navigateByUrl('/');
      return;
    }

    const redirect = this.route.snapshot.queryParamMap.get('redirect') ?? '/';
    this.redirectPath = redirect.startsWith('/') ? redirect : '/';
    this.email = this.auth.getPendingEmail();
  }

  async sendLink(): Promise<void> {
    this.error = '';
    this.sentTo = '';
    this.loading = true;

    try {
      await this.auth.sendEmailLoginLink(this.email, this.redirectPath);
      this.sentTo = this.email.trim();
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    } finally {
      this.loading = false;
    }
  }
}

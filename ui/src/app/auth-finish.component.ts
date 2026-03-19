import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { AuthService } from './auth.service';

@Component({
  selector: 'dop-auth-finish',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './auth-finish.component.html',
  styleUrl: './auth-finish.component.css',
})
export class AuthFinishComponent implements OnInit {
  email = '';
  loading = true;
  needEmailConfirmation = false;
  error = '';

  constructor(
    private readonly auth: AuthService,
    private readonly route: ActivatedRoute,
    private readonly router: Router,
  ) {}

  async ngOnInit(): Promise<void> {
    await this.tryComplete();
  }

  async confirmEmailAndComplete(): Promise<void> {
    this.error = '';
    this.loading = true;
    try {
      await this.auth.completeEmailLinkLogin(window.location.href, this.email);
      await this.router.navigateByUrl(this.nextPath());
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
      this.loading = false;
    }
  }

  private async tryComplete(): Promise<void> {
    this.loading = true;
    this.error = '';

    if (!this.auth.isEmailLink(window.location.href)) {
      this.error = 'This sign-in link is invalid.';
      this.loading = false;
      return;
    }

    try {
      await this.auth.completeEmailLinkLogin(window.location.href);
      await this.router.navigateByUrl(this.nextPath());
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.error = message;
      this.needEmailConfirmation = message.toLowerCase().includes('email confirmation required');
      this.loading = false;
    }
  }

  private nextPath(): string {
    const redirect = this.route.snapshot.queryParamMap.get('redirect') ?? '/';
    if (!redirect.startsWith('/')) {
      return '/';
    }
    return redirect;
  }
}

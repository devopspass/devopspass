import { Injectable } from '@angular/core';
import {
  Auth,
  User,
  browserLocalPersistence,
  getAuth,
  isSignInWithEmailLink,
  onAuthStateChanged,
  sendSignInLinkToEmail,
  setPersistence,
  signInWithEmailLink,
  signOut,
} from 'firebase/auth';
import { FirebaseApp, getApp, getApps, initializeApp } from 'firebase/app';

import { environment } from '../environments/environment';

const PENDING_EMAIL_STORAGE_KEY = 'dop.auth.pendingEmail';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly app: FirebaseApp;
  private readonly auth: Auth;
  private currentUser: User | null = null;
  private readonly listeners = new Set<(user: User | null) => void>();
  private readyResolved = false;
  private readonly readyPromise: Promise<void>;
  private resolveReady!: () => void;

  constructor() {
    this.app = getApps().length > 0 ? getApp() : initializeApp(environment.firebase);
    this.auth = getAuth(this.app);
    this.auth.languageCode = 'en';

    this.readyPromise = new Promise<void>((resolve) => {
      this.resolveReady = resolve;
    });

    // Persist auth across browser restarts as requested.
    void setPersistence(this.auth, browserLocalPersistence);

    onAuthStateChanged(this.auth, (user) => {
      this.currentUser = user;
      this.emitAuthChanged(user);
      if (!this.readyResolved) {
        this.readyResolved = true;
        this.resolveReady();
      }
    });
  }

  async waitForAuthReady(): Promise<void> {
    await this.readyPromise;
  }

  isLoggedIn(): boolean {
    return this.currentUser !== null || this.auth.currentUser !== null;
  }

  getUser(): User | null {
    return this.currentUser ?? this.auth.currentUser;
  }

  getPendingEmail(): string {
    return localStorage.getItem(PENDING_EMAIL_STORAGE_KEY) ?? '';
  }

  async getIdToken(forceRefresh = false): Promise<string | null> {
    await this.waitForAuthReady();
    const user = this.currentUser ?? this.auth.currentUser;
    if (!user) {
      return null;
    }
    return user.getIdToken(forceRefresh);
  }

  async sendEmailLoginLink(email: string, redirectPath: string): Promise<void> {
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      throw new Error('Email is required.');
    }

    const continueUrl = new URL(environment.authEmailLinkPath, window.location.origin);
    if (redirectPath && redirectPath !== '/') {
      continueUrl.searchParams.set('redirect', redirectPath);
    }

    await sendSignInLinkToEmail(this.auth, trimmedEmail, {
      url: continueUrl.toString(),
      handleCodeInApp: true,
    });

    localStorage.setItem(PENDING_EMAIL_STORAGE_KEY, trimmedEmail);
  }

  isEmailLink(url: string): boolean {
    return isSignInWithEmailLink(this.auth, url);
  }

  async completeEmailLinkLogin(url: string, email?: string): Promise<User> {
    if (!isSignInWithEmailLink(this.auth, url)) {
      throw new Error('This login link is invalid or expired.');
    }

    const pendingEmail = (email ?? this.getPendingEmail()).trim();
    if (!pendingEmail) {
      throw new Error('Email confirmation required. Please enter the same email used to request the link.');
    }

    const result = await signInWithEmailLink(this.auth, pendingEmail, url);
    this.currentUser = result.user;
    this.emitAuthChanged(this.currentUser);
    localStorage.removeItem(PENDING_EMAIL_STORAGE_KEY);
    return result.user;
  }

  async logout(): Promise<void> {
    await signOut(this.auth);
  }

  onAuthChanged(listener: (user: User | null) => void): () => void {
    this.listeners.add(listener);
    listener(this.currentUser);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emitAuthChanged(user: User | null): void {
    for (const listener of this.listeners) {
      listener(user);
    }
  }
}

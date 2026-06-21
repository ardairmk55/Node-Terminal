// Tarayıcı kapalıyken arka planda işletim sisteminden gelen Push paketini yakalar
self.addEventListener('push', function(event) {
    if (event.data) {
        try {
            const payload = event.data.json();
            const options = {
                body: payload.body,
                icon: '/static/icons/icon-192.png', // Varsa uygulamanın ikonu
                badge: '/static/icons/icon-192.png',
                vibrate: [200, 100, 200], // Telefonu titretir
                data: { dateOfArrival: Date.now() },
                actions: [
                    { action: 'open', title: 'Terminali Aç' }
                ]
            };

            event.waitUntil(
                self.registration.showNotification(payload.title, options)
            );
        } catch (e) {
            // Eğer düz metin geldiyse
            const options = { body: event.data.text() };
            event.waitUntil(
                self.registration.showNotification('Node Sinyali', options)
            );
        }
    }
});

// Bildirime tıklandığında ne olacağını belirler
self.addEventListener('notificationclick', function(event) {
    event.notification.close(); // Bildirimi kapat
    
    // Uygulama kapalıysa aç, açıksa sekmeye odaklan
    event.waitUntil(
        clients.matchAll({ type: 'window' }).then(function(clientList) {
            for (let i = 0; i < clientList.length; i++) {
                let client = clientList[i];
                if (client.url === '/' && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow('/');
            }
        })
    );
});
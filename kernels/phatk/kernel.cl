// This file is taken and modified from the public-domain poclbm project, and
// we have therefore decided to keep it public-domain in Phoenix.

// 2011-07-01: further modified by Diapolo and still public-domain

#ifdef VECTORS
	typedef uint2 u;
#else
	typedef uint u;
#endif

__constant uint K[64] = { 
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
};

__constant uint H[8] = { 
	0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
};

#ifdef BITALIGN
	#pragma OPENCL EXTENSION cl_amd_media_ops : enable
	#define rot(x, y) amd_bitalign(x, x, (u)(32 - y))
#else
	#define rot(x, y) rotate(x, (u)y)
#endif

// This part is not from the stock poclbm kernel. It's part of an optimization
// added in the Phoenix Miner.

// Some AMD devices have the BFI_INT opcode, which behaves exactly like the
// SHA-256 Ch function, but provides it in exactly one instruction. If
// detected, use it for Ch. Otherwise, construct Ch out of simpler logical
// primitives.

#ifdef BFI_INT
	// Well, slight problem... It turns out BFI_INT isn't actually exposed to
	// OpenCL (or CAL IL for that matter) in any way. However, there is 
	// a similar instruction, BYTE_ALIGN_INT, which is exposed to OpenCL via
	// amd_bytealign, takes the same inputs, and provides the same output. 
	// We can use that as a placeholder for BFI_INT and have the application 
	// patch it after compilation.
	
	// This is the BFI_INT function
	#define Ch(x, y, z) amd_bytealign(x, y, z)
	// Ma can also be implemented in terms of BFI_INT...
	#define Ma(x, y, z) amd_bytealign((z ^ x), y, x)
#else
	#define Ch(x, y, z) (z ^ (x & (y ^ z)))
	#define Ma(x, y, z) ((x & z) | (y & (x | z)))
#endif

// Various intermediate calculations for each SHA round
#define s0(n) (rot(Vals[(128 - n) % 8], 30) ^ rot(Vals[(128 - n) % 8], 19) ^ rot(Vals[(128 - n) % 8], 10))
#define s1(n) (rot(Vals[(132 - n) % 8], 26) ^ rot(Vals[(132 - n) % 8], 21) ^ rot(Vals[(132 - n) % 8], 7))
#define ch(n) (Ch(Vals[(132 - n) % 8], Vals[(133 - n) % 8], Vals[(134 - n) % 8]))
#define maj(n) (Ma(Vals[(129 - n) % 8], Vals[(130 - n) % 8], Vals[(128 - n) % 8]))
#define t1(n) (Vals[(135 - n) % 8] + K[n % 64] + W[n] + ch(n) + s1(n))
#define t1W(n) (Vals[(135 - n) % 8] + K[n % 64] + w(n) + ch(n) + s1(n))
#define t2(n) (s0(n) + maj(n))

// W calculation used for SHA round
#define w(n) (W[n] = P1(n) + P2(n) + P3(n) + P4(n))

// Full W calculation
#define R(x) (W[x] = (rot(W[x - 2], 15) ^ rot(W[x - 2], 13) ^ ((W[x - 2]) >> 10U)) + W[x - 7] + (rot(W[x - 15], 25) ^ rot(W[x - 15], 14) ^ ((W[x - 15]) >> 3U)) + W[x - 16])

// Partial W calculations (used for the begining where only some values are nonzero)
#define r0(x) ((rot(x, 25) ^ rot(x, 14) ^ (x >> 3U)))
#define r1(x) ((rot(x, 15) ^ rot(x, 13) ^ (x >> 10U)))
#define R0(n) ((rot(W[n], 25) ^ rot(W[n], 14) ^ (W[n] >> 3U)))
#define R1(n) ((rot(W[n], 15) ^ rot(W[n], 13) ^ (W[n] >> 10U)))
#define P1(x) R1(x - 2)
#define P2(x) R0(x - 15)
#define P3(x) W[x - 7]
#define P4(x) W[x - 16]

// SHA round with built in W calc
#define sharound2(n) { Vals[(131 - n) % 8] += t1W(n); Vals[(135 - n) % 8] = t1W(n) + t2(n); }
// SHA round without W calc
#define sharound(n) { t1 = t1(n); Vals[(131 - n) % 8] += t1(n); Vals[(135 - n) % 8] = t1(n) + t2(n); }

// Partial SHA calculations (used for begining and end)
#define partround(n) { Vals[(135 - n) % 8] = (Vals[(135 - n) % 8] + W[n]); Vals[(131 - n) % 8] += Vals[(135 - n) % 8]; Vals[(135 - n) % 8] += t1; }

__kernel void search(	const uint state0, const uint state1, const uint state2, const uint state3,
						const uint state4, const uint state5, const uint state6, const uint state7,
						const uint B1, const uint C1, const uint D1,
						const uint F1, const uint G1, const uint H1,
						const uint base,
						const uint W2,
						const uint W16, const uint W17,
						const uint PreVal4, const uint T1,
						__global uint * output)
{
	u W[128];
	u Vals[8];
	u t1 = T1;

	Vals[0] = state0;
	Vals[1] = B1;
	Vals[2] = C1;
	Vals[3] = D1;
	Vals[4] = PreVal4;
	Vals[5] = F1;
	Vals[6] = G1;
	Vals[7] = H1;

	W[2] = W2;
#ifdef VECTORS 
	W[3] = ((base + get_global_id(0)) << 1) + (uint2)(0, 1);
#else
	W[3] = base + get_global_id(0);
#endif
	W[4] = 0x80000000U;
	W[14] = W[13] = W[12] = W[11] = W[10] = W[9] = W[8] = W[7] = W[6] = W[5] = 0x00000000U;
	W[15] = 0x00000280U;
	W[16] = W16;
	W[17] = W17;
	W[18] = P1(18) + P3(18) + P4(18) + P2(18);
	W[19] = P1(19) + P2(19) + P3(19) + P4(19);
	W[20] = P2(20) + P3(20) + P4(20) + P1(20);
	W[21] = P1(21);
	W[22] = P3(22) + P1(22);
	W[23] = P3(23) + P1(23);
	W[24] = P1(24) + P3(24);
	W[25] = P1(25) + P3(25);
	W[26] = P1(26) + P3(26);
	W[27] = P1(27) + P3(27);
	W[28] = P1(28) + P3(28);
	W[29] = P1(29) + P3(29);
	W[30] = P1(30) + P2(30) + P3(30);
	W[31] = P2(31) + P4(31) + P1(31) + P3(31);
	W[32] = P2(32) + P4(32) + P1(32) + P3(32);
	
	partround(3);
	sharound(4);
	sharound(5);
	sharound(6);
	sharound(7);
	sharound(8);
	sharound(9);
	sharound(10);
	sharound(11);
	sharound(12);
	sharound(13);
	sharound(14);
	sharound(15);
	sharound(16);
	sharound(17);
	sharound(18);
	sharound(19);
	sharound(20);
	sharound(21);
	sharound(22);
	sharound(23);
	sharound(24);
	sharound(25);
	sharound(26);
	sharound(27);
	sharound(28);
	sharound(29);
	sharound(30);
	sharound(31);
	sharound(32);
	sharound2(33);
	sharound2(34);
	sharound2(35);
	sharound2(36);
	sharound2(37);
	sharound2(38);
	sharound2(39);
	sharound2(40);
	sharound2(41);
	sharound2(42);
	sharound2(43);
	sharound2(44);
	sharound2(45);
	sharound2(46);
	R(47);
	sharound(47);
	R(48);
	sharound(48);
	R(49);
	sharound(49);
	R(50);
	sharound(50);
	R(51);
	sharound(51);
	R(52);
	sharound(52);
	R(53);
	sharound(53);
	R(54);
	sharound(54);
	R(55);
	sharound(55);
	R(56);
	sharound(56);
	R(57);
	sharound(57);
	R(58);
	sharound(58);
	R(59);
	sharound(59);
	R(60);
	sharound(60);
	R(61);
	sharound(61);
	sharound2(62);
	sharound2(63);

	W[64] = state0 + Vals[0];
	W[65] = state1 + Vals[1];
	W[66] = state2 + Vals[2];
	W[67] = state3 + Vals[3];
	W[68] = state4 + Vals[4];
	W[69] = state5 + Vals[5];
	W[70] = state6 + Vals[6];
	W[71] = state7 + Vals[7];

	W[72] = 0x80000000U;
	W[78] = W[77] = W[76] = W[75] = W[74] = W[73] = 0x00000000U;
	W[79] = 0x00000100U;

	Vals[0] = H[0];
	Vals[1] = H[1];
	Vals[2] = H[2];
	Vals[3] = (u)0x198c7e2a2 + W[64];
	Vals[4] = H[4];
	Vals[5] = H[5];
	Vals[6] = H[6];
	Vals[7] = (u)0xfc08884d + W[64];
	
	R(80);

	sharound(65);
	sharound(66);
	W[81] = P1(81) + P2(81) + P4(81);
	W[82] = P1(82) + P2(82) + P4(82);
	sharound(67);
	W[83] = P1(83) + P2(83) + P4(83);
	sharound(68);
	W[84] = P1(84) + P2(84) + P4(84);
	sharound(69);
	W[85] = P1(85) + P2(85) + P4(85);
	sharound(70);
	R(86);
	sharound(71);
	sharound(72);
	R(87);
	W[88] = P1(88) + P3(88) + P4(88);
	sharound(73);
	sharound(74);
	W[89] = P1(89) + P3(89);
	W[90] = P1(90) + P3(90);
	sharound(75);
	sharound(76);
	W[91] = P1(91) + P3(91);
	W[92] = P1(92) + P3(92);
	sharound(77);
	sharound(78);
	sharound(79);
	sharound(80);
	sharound(81);
	sharound(82);
	sharound(83);
	sharound(84);
	sharound(85);
	sharound(86);
	sharound(87);
	sharound(88);
	sharound(89);
	sharound(90);
	sharound(91);
	sharound(92);
	sharound2(93);
	sharound2(94);
	sharound2(95);
	sharound2(96);
	sharound2(97);
	sharound2(98);
	sharound2(99);
	sharound2(100);
	sharound2(101);
	sharound2(102);
	sharound2(103);
	sharound2(104);
	sharound2(105);
	sharound2(106);
	sharound2(107);
	sharound2(108);
	sharound2(109);
	sharound2(110);
	sharound2(111);
	sharound2(112);
	sharound2(113);
	R(114);
	sharound(114);
	R(115);
	sharound(115);
	R(116);
	sharound(116);
	R(117);
	sharound(117);
	R(118);
	sharound(118);
	R(119);
	sharound(119);
	sharound2(120);
	sharound2(121);
	sharound2(122);
	sharound2(123);

	// Faster to write it this way...
	Vals[3] += K[60] + s1(124) + ch(124);
	R(124);
	partround(124);

#ifdef VECTORS
	if(Vals[7].x == -H[7])
	{	
		output[OUTPUT_SIZE] = output[(W[3].x >> 2) & OUTPUT_MASK] = W[3].x;
		
	}
	if(Vals[7].y == -H[7])
	{
		output[OUTPUT_SIZE] = output[(W[3].y >> 2) & OUTPUT_MASK] =  W[3].y;
	}
#else
	if(Vals[7] == -H[7])
	{
		output[OUTPUT_SIZE] = output[(W[3] >> 2) & OUTPUT_MASK] = W[3];
	}
#endif
}